"""Tutorial 04 -- a mesh of agents, routed with zero routing-LLM (4 min).

Multiple agents, one entry point. graxella routes each task to the right
agent by semantic match on what the agents SAY they do (their docstring is
their skill card). The routing decision is deterministic and recorded --
no supervisor LLM burning tokens to pick a name.

Agents here are plain callables so the tutorial runs anywhere; native
LangGraph agents from create_agent() drop into the same grx.mesh([...])
unchanged.

Run:  python tutorials/04_agent_mesh.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "graxella"))

import graxella

grx = graxella.Session("mesh-demo", domain="tutorial", workdir="ephemeral")


# The docstring is the agent's skill card -- routing reads it.
def orders_agent(task: str) -> dict:
    """track shipments, deliveries and order status by order number"""
    return {"result": f"orders_agent handling: {task}"}


def billing_agent(task: str) -> dict:
    """billing questions, invoice charges, refunds, overcharged payments"""
    return {"result": f"billing_agent handling: {task}"}


# One line: wrap them into a governed mesh. Each agent is auto-told what
# its peers can do, so it can suggest handoffs.
#
# Routing note: the default router is embedding-first ("auto") -- with a
# local Ollama or sentence-transformers present, paraphrases route
# semantically; with neither, it falls back to lexical word overlap and
# SAYS SO loudly. And when NO agent matches, graxella refuses to route
# (a self-explaining NoRouteError with the near-miss scores) rather than
# guessing -- misrouting silently is worse than failing loudly.
app = grx.mesh([orders_agent, billing_agent])

for task in ["where is my delivery for order 1234?",
             "I was overcharged on my invoice charges"]:
    out = app.invoke({"messages": [("user", task)]})
    r = out["route"]
    print(f"[-> {r['agent']:<13} score={r['score']:.2f}] "
          f"{out['messages'][-1]['content']}")

# Multi-hop, safely: bounded hops, loop detection, budget -- a runaway
# chain escalates to a human instead of spinning.
t = app.run_trajectory("track order 1234 and check its invoice", max_hops=3)
print(f"\ntrajectory: {t.n_hops} hop(s), status={t.status}, "
      f"escalated={t.escalated}")

# Every route was recorded as a decision + outcome:
print(f"ledger now holds {grx.stats()['total']['count']} recorded outcomes")

# What you learned:
#   app = grx.mesh([a, b])      -> agents in, governed mesh out
#   routing is deterministic     -> auditable, zero routing-LLM cost
#   app.run_trajectory(...)      -> multi-hop with loop/budget containment
#
# Next: 05_memory_recall.py -- the agent that remembers what worked.
