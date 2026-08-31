"""Smoke test: LangGraph supervisor consulting two remote A2A agents as tools."""
import asyncio
import time

import httpx
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from a2a.client import ClientConfig, create_client
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest

DIAG_URL = "http://127.0.0.1:9101"
REMED_URL = "http://127.0.0.1:9102"
MODEL = "qwen2.5:7b"

_clients: dict[str, object] = {}


async def a2a_ask(url: str, text: str) -> str:
    """One A2A request/response round trip (blocking until the remote agent finishes)."""
    if url not in _clients:
        cfg = ClientConfig(httpx_client=httpx.AsyncClient(timeout=httpx.Timeout(600.0)))
        _clients[url] = await create_client(url, cfg)
    req = SendMessageRequest(message=new_text_message(text, role=Role.ROLE_USER))
    chunks = [get_stream_response_text(r) async for r in _clients[url].send_message(req)]
    return "\n".join(c for c in chunks if c) or "(remote agent returned no text)"


@tool
async def consult_diagnostics(incident_description: str) -> str:
    """Ask the remote diagnostics agent to find the root cause of an incident.
    Pass the full incident description; it has live metrics, dependency maps and deploy history."""
    print(f"\n>>> A2A -> diagnostics-agent: {incident_description[:100]!r}")
    out = await a2a_ask(DIAG_URL, incident_description)
    print(f"<<< diagnostics-agent replied ({len(out)} chars)")
    return out


@tool
async def consult_remediation(diagnosed_root_cause: str) -> str:
    """Ask the remote remediation agent for a runbook-grounded fix plan + incident ticket.
    Pass the diagnosed root cause (service, version, causal chain)."""
    print(f"\n>>> A2A -> remediation-agent: {diagnosed_root_cause[:100]!r}")
    out = await a2a_ask(REMED_URL, diagnosed_root_cause)
    print(f"<<< remediation-agent replied ({len(out)} chars)")
    return out


SUPERVISOR_PROMPT = """You are the incident commander. You do NOT have direct access
to metrics or runbooks - you must delegate:
1. First call consult_diagnostics with the incident description to get the root cause.
2. Then call consult_remediation, passing the diagnosed root cause verbatim.
3. Finally write the incident summary: root cause, evidence, plan, ticket id.
Call each tool at most twice. Do not invent facts the specialists did not report."""

INCIDENT = """INCIDENT: since 09:40 UTC checkout-api p99 latency is 4.2s (baseline 0.5s)
and error rate is 12%. Customers cannot complete payments. Find the root cause and
produce a remediation plan with an incident ticket."""


async def main():
    supervisor = create_react_agent(
        ChatOllama(model=MODEL, temperature=0),
        [consult_diagnostics, consult_remediation],
        prompt=SUPERVISOR_PROMPT,
    )
    t0 = time.time()
    result = await supervisor.ainvoke(
        {"messages": [{"role": "user", "content": INCIDENT}]},
        {"recursion_limit": 20},
    )
    print(f"\n=== finished in {time.time()-t0:.0f}s, {len(result['messages'])} messages ===")
    for m in result["messages"]:
        tc = getattr(m, "tool_calls", None)
        label = m.type + (f" -> {[t['name'] for t in tc]}" if tc else "")
        print(f"[{label}] {str(m.content)[:150]!r}")
    print("\n=== FINAL ANSWER ===")
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
