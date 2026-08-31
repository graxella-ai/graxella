"""Tutorial 08 -- a mesh of native LangGraph agents (10 min, needs Ollama).

Several LangChain/LangGraph agents, one governed front door. You build the
agents exactly as the LangChain docs show -- create_agent(llm, tools,
name=...) -- and hand the list to grx.mesh(). graxella then:

    * routes each request to the right agent -- deterministically, by
      semantic match on their tools (no supervisor LLM),
    * injects a peer directory so each agent knows who it works with,
    * records every dispatch as a decision + typed outcome,
    * and keeps multi-hop chains bounded (loop detection + budgets).

Requires: a local Ollama with `qwen2.5:3b`  (ollama pull qwen2.5:3b)

Run:  python tutorials/08_langgraph_mesh.py
"""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "graxella"))

try:
    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
        assert r.status == 200
except Exception:
    print("This tutorial needs a local Ollama with qwen2.5:3b.")
    print("Tutorials 01-06 run without any LLM.")
    sys.exit(0)

import graxella
from langchain.agents import create_agent
from langchain_ollama import ChatOllama

grx = graxella.Session("helpdesk", domain="support",
                       model_id="qwen2.5:3b", workdir="ephemeral")

# ---- governed tools (same pattern as tutorial 07) -----------------------
@grx.tool
def track_order(order_id: str) -> str:
    """track a shipment / delivery status by order number"""
    return f"order {order_id}: in transit, arriving Tuesday"

@grx.tool
def refund_status(order_id: str) -> str:
    """check the refund and billing status for an order"""
    return f"order {order_id}: refund approved, 3-5 business days"

# ---- two NATIVE LangGraph agents, built exactly like the LangChain docs -
llm = ChatOllama(model="qwen2.5:3b", temperature=0)
orders   = create_agent(llm, [track_order],   name="orders")
billing  = create_agent(llm, [refund_status], name="billing")

# ---- one line of graxella: the governed mesh ----------------------------
app = grx.mesh([orders, billing])

for task in ["where is my order 1234?",
             "what's the refund status for order 1234?"]:
    out = app.invoke({"messages": [("user", task)]})
    r = out["route"]
    print(f"[routed -> {r['agent']:<8} score={r['score']:.2f}] "
          f"{out['messages'][-1]['content'][:90]}")

# ---- the governance ledger ----------------------------------------------
stats = grx.stats()
print(f"\nledger: {stats['total']['count']} outcomes "
      f"(routing decisions AND tool calls, one substrate)")
print("routing was deterministic -- the only LLM calls were the agents "
      "doing their actual work.")

# What you learned:
#   create_agent(...) agents drop into grx.mesh() unchanged
#   routing costs zero LLM tokens and every dispatch is recorded
#   tools inside the agents are the SAME governed tools from 01-07,
#   so healing + review + audit all keep working at mesh scale
#
# That's the full path: examples/ goes deeper (autonomous promotion,
# provenance audits, benchmarks with honest numbers).
