"""Tutorial 09 -- agent-to-agent handoffs, governed (4 min, no LLM needed).

Agents rarely finish a task alone: triage receives, a specialist executes.
Ungoverned, that handoff is prompt glue -- untyped, unlogged, and one bad
loop away from two agents bouncing a task forever.

graxella's A2A layer gives every mesh agent the same tiny protocol, for
free (it is injected as peer context -- you configure nothing):

    HANDOFF: <peer> :: <task for them>

and then governs what happens next:
    * every hop is routed + recorded (decision, outcome, explanation)
    * a repeated (agent, response) state is a LOOP -> stopped + escalated
    * hop / token / wallclock budgets contain runaways
    * the whole chain is one auditable ledger object

Agents here are plain callables so the mechanics are guaranteed visible;
native LangChain agents (tutorials 07-08) receive the same peer directory
and emit the same HANDOFF line -- identical governance, zero extra code.

Run:  python tutorials/09_agent_handoff.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "graxella"))

import graxella

grx = graxella.Session("handoff-demo", domain="support", workdir="ephemeral")


# ---- ACT 1: a clean, governed handoff -----------------------------------
# Triage receives customer messages and dispatches; orders executes.
def triage(task: str) -> dict:
    """handle incoming customer requests from the inbox and dispatch them"""
    if "order" in str(task).lower():
        return {"result": "this is a shipment question - "
                          "HANDOFF: orders :: track order 1234"}
    return {"result": "triage: resolved directly"}


def orders(task: str) -> dict:
    """track shipments and delivery status by order number"""
    return {"result": "order 1234: in transit, arriving Tuesday"}


app = grx.mesh([triage, orders])

print("ACT 1 - a customer message that needs two agents")
t = app.run_trajectory("incoming customer request from the inbox: "
                       "an order issue", max_hops=4)
for h in t.hops:
    print(f"  hop {h.seq}: {h.agent:<7} ok={h.ok}  task={h.task[:44]!r}")
print(f"  status={t.status}  final: {t.final_response[:60]!r}")
print(f"  the chain is ledger object {t.assertion_id[:16]}... "
      f"(cites every hop's decision)\n")


# ---- ACT 2: a runaway loop -- caught, stopped, escalated ----------------
# Two agents that (buggily) keep handing the task to each other. This is
# MAST's most frequent multi-agent failure mode. Watch graxella stop it.
grx2 = graxella.Session("rally-demo", domain="game", workdir="ephemeral")


def ping(task: str) -> dict:
    """start the rally and serve the ball"""
    return {"result": "ping! HANDOFF: pong :: keep rallying and return the ball"}


def pong(task: str) -> dict:
    """keep rallying and return the ball"""
    return {"result": "pong! HANDOFF: ping :: serve the ball again"}


app2 = grx2.mesh([ping, pong])

print("ACT 2 - two buggy agents that hand off to each other forever")
t2 = app2.run_trajectory("start the rally and serve the ball", max_hops=10)
for h in t2.hops:
    print(f"  hop {h.seq}: {h.agent}")
print(f"  status={t2.status}  escalated={t2.escalated}")
sig = grx2.memory.signals(kind="trajectory_escalation")
print(f"  escalation signals on the ledger: {len(sig)} "
      "<- a human gets a cited alert, not a burning token bill")

# What you learned:
#   handoffs are TYPED (HANDOFF: peer :: task) and injected automatically
#   every hop = decision + outcome + explanation on one ledger
#   loops are detected by repeated state and STOPPED -- then escalated
#   budgets (hops/tokens/wallclock) contain what detection can't foresee
#
# This is the difference between "agents can talk" and "agents can talk,
# and you can prove what they said, why, and when it was stopped."
