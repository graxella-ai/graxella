"""A2A society routing + hidden-DSPy tool healing -- the full stack, one Session.

The developer authors only: two @grx.tool functions, two plain agent
callables, and grx.mesh([...]). No healer, no DSPy, no routing rules, no
handoff protocol. graxella supplies all of that underneath.

The whole pipeline makes exactly ONE llm call -- the drift heal:

    routing between agents  ->  A2A society, deterministic TF-IDF, zero LLM
    agent dispatch          ->  deterministic
    a drifted tool          ->  DSPy proposes a repair recipe ONCE, which is
                                then applied deterministically and shipped to
                                the Evidence Gate for human review

Success criteria: run as-is, this prints three routed replies (the first
healed), `healer (LLM) calls, ever: 1`, and one transform proposal pending
review -- all from a single governed ledger.

Requires a local Ollama with `qwen2.5:3b`. The healer prefers DSPy
(`pip install "graxella[heal]"`); without it, it falls back to Ollama's
JSON mode -- same result, no code change.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

# Path shim so the demo runs before graxella is pip-installed.
_PKG = Path(__file__).resolve().parents[1] / "packages" / "graxella"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

import graxella  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")


def main() -> None:
    grx = graxella.Session("support-desk", domain="support",
                           model_id="qwen2.5:3b", workdir="ephemeral")

    # ---- tools ---------------------------------------------------------
    # The shipping API migrated: 'order_id' is gone and the error hints the
    # new field name. shipping_v2 is the new-schema endpoint (it takes the
    # POST-transform args). No healer is authored -- graxella heals it.
    def shipping_v2(args: dict) -> str:
        return f"in transit, arriving Tue (ref {args['order_ref']})"

    @grx.tool(fallback=shipping_v2)
    def get_shipping_status(order_id: str) -> str:
        """get the delivery status of an order by its id"""
        raise TypeError("unexpected keyword argument 'order_id'; schema "
                        "deprecated - use 'order_ref' instead")

    @grx.tool
    def get_weather(city: str) -> str:
        """get the current weather for a city"""
        return "sunny, 27C"

    # ---- agents (plain callables; the docstring is the A2A skill card) --
    def orders_agent(task: str) -> dict:
        """track shipments, deliveries and order status by order number"""
        m = re.search(r"\b(\d{3,})\b", task)
        oid = m.group(1) if m else "0000"
        status = get_shipping_status.invoke({"order_id": oid})
        return {"result": f"Order {oid}: {status}",
                "tool_calls": [{"name": "get_shipping_status"}]}

    def weather_agent(task: str) -> dict:
        """report the current weather and temperature for a city or location"""
        m = re.search(r"in ([A-Z][a-z]+)", task)
        city = m.group(1) if m else "your city"
        return {"result": f"{city}: {get_weather.invoke({'city': city})}",
                "tool_calls": [{"name": "get_weather"}]}

    # ---- the A2A society: deterministic routing, governed underneath ----
    app = grx.mesh([orders_agent, weather_agent])

    tasks = [
        "where is my delivery for order 1234?",  # -> orders; tool drifts, heals
        "what's the weather in Chennai?",         # -> weather
        "track order 5678 please",                # -> orders; recipe reused, no LLM
    ]

    print("\n=== A2A routing + healing (1 llm call total: the heal) ===")
    for t in tasks:
        out = app.invoke({"messages": [("user", t)]})
        route = out["route"]
        reply = out["messages"][-1]["content"]
        print(f"[route -> {route['agent']:<13} score={route['score']:.2f}] {reply}")

    print("\n=== governance / evidence (one ledger) ===")
    print("healer (LLM) calls, ever:", grx.healer_calls)
    pend = grx.pending()
    print("proposals pending human review:", len(pend))
    for p in pend:
        print("  -", p.get("kind"), "|", (p.get("reason") or "")[:70])

    assert grx.healer_calls == 1, f"LLM fired {grx.healer_calls}x (expected 1)"
    assert len(pend) >= 1, "expected a transform proposal from the heal"
    print("\nOK - A2A routed deterministically; the only LLM call was the "
          "one-shot DSPy heal; recipe cached + queued for review.")


if __name__ == "__main__":
    main()
