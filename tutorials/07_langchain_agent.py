"""Tutorial 07 -- governing a NATIVE LangChain agent (8 min, needs Ollama).

You already have a LangChain agent. This tutorial shows what changes when
graxella governs it: nothing in your agent code -- and everything in what
you can see, heal, and audit.

The recipe:
    1. write tools with @grx.tool          (the ONLY graxella line you add)
    2. build your agent with langchain.agents.create_agent -- unchanged
    3. run it -- and read the governance you got for free

While the agent runs, one of its tools drifts (the upstream API renamed a
field). You wrote NO healer: graxella's built-in engine proposes the repair
once, applies it deterministically, and queues it for your review.

Requires: a local Ollama (https://ollama.com) with `qwen2.5:3b`:
    ollama pull qwen2.5:3b

Run:  python tutorials/07_langchain_agent.py
"""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "graxella"))

# ---- graceful gate: this tutorial needs a real local LLM ----------------
try:
    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
        assert r.status == 200
except Exception:
    print("This tutorial needs a local Ollama with qwen2.5:3b.")
    print("  install: https://ollama.com   then:  ollama pull qwen2.5:3b")
    print("Tutorials 01-06 run without any LLM.")
    sys.exit(0)

import graxella
from langchain.agents import create_agent
from langchain_ollama import ChatOllama

grx = graxella.Session("support-desk", domain="support",
                       model_id="qwen2.5:3b", workdir="ephemeral")

# ---- 1. tools: plain functions + one decorator --------------------------
@grx.tool
def check_order(order_id: str) -> str:
    """look up an order's items and payment status by order id"""
    return f"order {order_id}: 2 items, paid, packed"

# This tool was written against the OLD shipping API, which migrated
# today. The drift is GENUINE: the carrier's v2 client validates requests
# with pydantic, so old-schema args raise a real ValidationError -- no
# error string was crafted for graxella's detector, and NO healer is
# written anywhere in this file. The built-in healer reads pydantic's own
# message; an unambiguous rename like this one is repaired
# DETERMINISTICALLY (zero model calls) -- the LLM engine only engages
# when the repair is ambiguous.
from pydantic import BaseModel

class ShipmentQuery(BaseModel):            # the carrier's v2 request schema
    order_ref: str

def shipping_v2(args: dict) -> str:
    q = ShipmentQuery(**args)
    return f"in transit, arriving Tuesday (ref {q.order_ref})"

@grx.tool(fallback=shipping_v2)
def get_shipping_status(order_id: str) -> str:
    """get the live shipping / delivery status of an order by its id"""
    return shipping_v2({"order_id": order_id})   # drifts for real

# ---- 2. your agent: 100% native LangChain, zero graxella ---------------
llm = ChatOllama(model="qwen2.5:3b", temperature=0)
agent = create_agent(llm, [check_order, get_shipping_status], name="support")

# ---- 3. run it ----------------------------------------------------------
question = ("Where is order 1234? Use get_shipping_status to check the "
            "delivery, then answer in one sentence.")
print(f"user: {question}\n")
result = agent.invoke({"messages": [("user", question)]})
print(f"agent: {result['messages'][-1].content}\n")

# ---- 4. the governance you got for free ---------------------------------
print("=" * 60)
print("what graxella recorded while your native agent ran:")
print("=" * 60)
stats = grx.stats()["total"]
print(f"  tool calls on the ledger : {stats['count']} (ok_rate={stats['ok_rate']})")
print(f"  healer repairs           : {grx.healer_calls}  <- fired ONCE, "
      "cached (deterministic here; the LLM only handles ambiguous drift)")
pending = grx.pending()
print(f"  fixes awaiting review    : {len(pending)}")
if pending:
    print("\nthe gate's cited reasoning (grx.why):")
    print(grx.why(pending[0]))

# What you learned:
#   your agent code is untouched LangChain -- graxella lives in the tools
#   a drifted tool healed itself mid-conversation, exactly once
#   the fix is NOT silently permanent: it awaits your approve/reject
#   (tutorial 03 showed that loop; it works the same here)
#
# Next: 08_langgraph_mesh.py -- several native agents, governed together.
