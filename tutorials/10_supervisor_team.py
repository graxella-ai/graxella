"""Tutorial 10 -- a supervisor with a team of specialist agents (12 min, Ollama).

The org chart enterprises actually deploy:

                        +-------------------+
                        |  supervisor (LLM) |   reads each request,
                        +---------+---------+   picks the specialist
                          picks   |
            +-----------------+---+-----------------+
            v                 v                     v
        +--------+       +---------+          +----------+
        | orders |       | billing |          | accounts |
        +--------+       +---------+          +----------+
        track_shipment   refund_status        unlock_account
        check_stock      invoice_lookup       update_address

Every specialist is a NATIVE LangChain agent (create_agent) with its own
tools. The supervisor is an LLM that only picks a name -- then graxella
dispatches, so every pick, every tool call, and every repair lands on one
evidence ledger.

The realistic wrinkle: mid-morning, the shipping carrier migrates its API
(v2 renames `order_id` -> `tracking_ref`). Nobody wrote a healer. Watch
what the customer sees -- and what the operator sees.

A model-sizing note, learned honestly: with a 3b model the specialists
sometimes misroute (hand a task to the wrong peer) -- and graxella's
ledger is exactly how you'd catch that. A team-of-agents org wants a
slightly stronger model, so this tutorial uses `qwen2.5:7b`.

Requires: a local Ollama with `qwen2.5:7b`  (ollama pull qwen2.5:7b)

Run:  python tutorials/10_supervisor_team.py
"""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "graxella"))

try:
    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
        assert r.status == 200
except Exception:
    print("This tutorial needs a local Ollama with qwen2.5:7b.")
    print("Tutorials 01-06 and 09 run without any LLM.")
    sys.exit(0)

import graxella
from langchain.agents import create_agent
from langchain_ollama import ChatOllama

grx = graxella.Session("support-org", domain="support",
                       model_id="qwen2.5:7b", workdir="ephemeral")

# ============================ the tools ==================================
# Plain functions + @grx.tool. Each call is recorded; drifts self-heal.

# -- orders team ----------------------------------------------------------
from pydantic import BaseModel

class TrackRequest(BaseModel):
    """The carrier's NEW v2 request schema (this morning's migration)."""
    tracking_ref: str

def carrier_v2(args: dict) -> str:
    req = TrackRequest(**args)             # genuine validation, not a prop
    return f"parcel {req.tracking_ref}: out for delivery, ETA today 6pm"

@grx.tool(fallback=carrier_v2)
def track_shipment(order_id: str) -> str:
    """track a shipment's live delivery status by order id"""
    return carrier_v2({"order_id": order_id})   # drifts for real

@grx.tool
def check_stock(sku: str) -> str:
    """check warehouse stock level for a product sku"""
    return f"sku {sku}: 14 units in the Chennai warehouse"

# -- billing team ---------------------------------------------------------
@grx.tool
def refund_status(order_id: str) -> str:
    """check the refund status for an order"""
    return f"refund for {order_id}: approved, settles in 3-5 business days"

@grx.tool
def invoice_lookup(order_id: str) -> str:
    """fetch the invoice and charges for an order"""
    return f"invoice {order_id}: 2499 INR, paid via UPI, no duplicate charges"

# -- accounts team --------------------------------------------------------
@grx.tool
def unlock_account(email: str) -> str:
    """unlock a customer account after failed sign-in attempts"""
    return f"account {email}: unlocked, reset link sent"

@grx.tool
def update_address(order_id: str, new_address: str) -> str:
    """change the delivery address on an open order"""
    return f"order {order_id}: delivery address updated to {new_address!r}"

# ======================= the specialist agents ===========================
# 100% native LangChain -- graxella does not appear in this section.
llm = ChatOllama(model="qwen2.5:7b", temperature=0)
orders   = create_agent(llm, [track_shipment, check_stock],    name="orders")
billing  = create_agent(llm, [refund_status, invoice_lookup],  name="billing")
accounts = create_agent(llm, [unlock_account, update_address], name="accounts")

# ========================= the supervisor ================================
# One line: an LLM supervisor over the team. It reads each request and
# picks a specialist; graxella routes, records, and governs the dispatch.
app = grx.supervisor([orders, billing, accounts], model=llm)

# ========================== a morning's tickets ==========================
tickets = [
    "Where is my order A-1042? It was supposed to arrive yesterday.",
    "I think I was double charged - can you check the invoice for A-1042?",
    "I'm locked out of my account, email is priya@example.com",
]

for msg in tickets:
    print(f"\ncustomer: {msg}")
    out = app.invoke({"messages": [("user", msg)]})
    r = out["route"]
    pick = r.get("supervisor_pick") or "(fell back to deterministic routing)"
    print(f"  [supervisor picked: {pick} -> dispatched to {r['agent']}]")
    print(f"  reply: {out['messages'][-1]['content'][:100]}")

# ===================== what the operator sees ============================
print("\n" + "=" * 62)
print("the operator's view (none of this needed extra code):")
print("=" * 62)
s = grx.stats()["total"]
print(f"  outcomes on the ledger    : {s['count']} "
      f"(supervisor picks + tool calls, ok_rate={s['ok_rate']})")
print(f"  drift repairs (healer)    : {grx.healer_calls}  "
      "<- the carrier migration, healed ONCE, invisibly to the customer "
      "(an unambiguous rename heals deterministically -- zero model calls; "
      "the LLM engine only engages for ambiguous drift)")
pending = grx.pending()
print(f"  fixes awaiting review     : {len(pending)}")

# Underneath the supervisor sits the A2A runtime (agent2society): every
# dispatch became a typed Handoff envelope with a persisted, replayable
# RoutingExplanation -- the supervisor LLM only NOMINATES a name; if it
# fails, deterministic routing catches the miss. This is the audit row:
exp = app.society.explanations(limit=1)[-1]
print(f"  A2A routing audit (last)  : agent={exp.chosen_agent} "
      f"skill={exp.chosen_skill} confidence={exp.confidence:.2f} "
      f"flags={list(exp.flags or [])}")
if pending:
    print("\n  the gate's cited verdict on the carrier fix:")
    for line in grx.why(pending[0]).splitlines()[:4]:
        print("   ", line)

# What you learned:
#   grx.supervisor([...], model=)  -> the boss-and-team org, one line
#   specialists are native create_agent(...) with their OWN toolboxes
#   the supervisor LLM only nominates; the A2A runtime (agent2society)
#     does the dispatch -- typed Handoff envelopes, persisted routing
#     explanations, and a deterministic fallback when the LLM misses
#   the carrier API broke mid-shift; the customer got a correct answer,
#   the operator got a cited, reviewable fix -- and nothing was silent
