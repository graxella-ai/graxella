"""MCP-flavored tools with realistic failure modes.

An MCP toolpoint can misbehave in ways that a healthy local Python function
never will. This module simulates the four that hurt the most in
production:

  1. DOWN            — endpoint unreachable / server crashed.
                       Raises ConnectionError on every call.
  2. HANGS           — endpoint accepts the call and never returns.
                       Sleeps longer than any reasonable timeout.
  3. FLAKY           — endpoint fails a fraction of the time with a
                       recoverable-looking error.
  4. DRIFTED         — endpoint returned malformed output, or a schema
                       the client can no longer parse; the tool raises
                       ValueError with a hint that the shape changed.

Plus one HEALTHY control tool and one BACKUP that acts as a substitute
for the DOWN one — so the demo can show the rulebook dispatching around
the outage rather than failing the whole request.

All tools are ordinary LangChain @tool decorators so they slot straight
into `bind_tools()` and any ReAct loop. The failure modes live in the
function bodies — no MCP client SDK required for the demo to be honest
about the behavior we want to protect against.
"""
from __future__ import annotations

import random
import time

from langchain_core.tools import tool


# Fixed seed keeps the flaky tool's failure pattern reproducible across runs.
_RNG = random.Random(42)


@tool
def mcp_get_weather(city: str, date: str = "today") -> str:
    """[MCP-healthy] Fetch weather for a city on a date. Returns a JSON-ish string."""
    return f"Weather in {city} on {date}: 18C partly cloudy"


@tool
def mcp_search_hotels_down(city: str, checkin: str, checkout: str) -> str:
    """[MCP-DOWN] Hotel search endpoint. Currently unreachable — server crashed.

    Every call raises ConnectionError to mimic an MCP transport failure.
    Substitute available: `mcp_search_hotels_backup(city, checkin, checkout)`.
    """
    raise ConnectionError(
        "mcp://hotels.example.com/search: connection refused (server down)"
    )


@tool
def mcp_search_hotels_backup(city: str, checkin: str, checkout: str) -> str:
    """[MCP-healthy backup] Hotel search on the failover provider. Same schema."""
    return (f"[backup] Hotels in {city} ({checkin} -> {checkout}):\n"
            f"  Fallback Inn      3* $110/night\n"
            f"  Redundant Suites  4* $175/night")


HANG_SECONDS = 8.0


@tool
def mcp_currency_hangs(amount: float, from_currency: str, to_currency: str) -> str:
    """[MCP-HANGS] Currency conversion endpoint. Latency has spiked to minutes.

    Sleeps for HANG_SECONDS to simulate an endpoint that accepts the call
    and never returns within any reasonable timeout. Kept at ~8s for the
    demo so a retry storm is unpleasant but not multi-minute; in production
    the same code path fires whether the hang is 8s or 8min.
    """
    time.sleep(HANG_SECONDS)
    return "unreachable-in-practice"


@tool
def mcp_translate_flaky(text: str, target_language: str) -> str:
    """[MCP-FLAKY] Translation endpoint. Fails ~60% of the time with 503.

    The failure pattern is deterministic via a seeded RNG so the demo
    reproduces the same trip-hammer test across runs.
    """
    if _RNG.random() < 0.6:
        raise RuntimeError("mcp://translate/v1: 503 Service Unavailable")
    return f"'{text}' in {target_language}: ありがとう (arigatou)"


@tool
def mcp_visa_drifted(from_country: str, to_country: str) -> str:
    """[MCP-DRIFTED] Visa lookup endpoint returned a payload we can't parse.

    Simulates a silent schema change on the provider side — the endpoint
    is up but the response shape moved (added a wrapper, renamed a field).
    """
    raise ValueError(
        "mcp://visa/lookup: expected {'required': bool, 'type': str} "
        "but got {'result': {'visa': {...}}} — schema drift"
    )


MCP_TOOLS = [
    mcp_get_weather,
    mcp_search_hotels_down,
    mcp_search_hotels_backup,
    mcp_currency_hangs,
    mcp_translate_flaky,
    mcp_visa_drifted,
]
MCP_TOOLS_BY_NAME = {t.name: t for t in MCP_TOOLS}


# Bundle used for the un-guarded (broken) run — hides the backup so the
# LLM cannot cheat by finding the substitute itself. Graxella must dispatch
# to it via the rulebook.
UNGUARDED_TOOLS = [
    mcp_get_weather,
    mcp_search_hotels_down,
    mcp_currency_hangs,
    mcp_translate_flaky,
    mcp_visa_drifted,
]
