"""Tutorial 06 -- the audit trail: "why?" and "what touched this?" (3 min).

The questions that block enterprise sign-off:
    "why did the agent do that?"          -> provenance, backward
    "what has touched order:1234?"        -> provenance, forward
    "which incident created this rule?"   -> both directions at once

graxella answers each in one call, from one ledger. No LLM anywhere.

Run:  python tutorials/06_audit_trail.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "graxella"))

import graxella

grx = graxella.Session("audit-demo", domain="support", workdir="ephemeral")
mem = grx.memory

# Simulate a day of work on one order, by different agents:
for task, agent in [
    ("triage the complaint about order 1234", "triage_agent"),
    ("check delivery status of order 1234", "orders_desk"),
    ("issue partial refund on order 1234", "refunds_desk"),
]:
    d = mem.record_decision(decision_type="delegate", task=task,
                            chosen=agent, domain="support")
    mem.record_outcome(decision_id=d, ok=True, kind="delegate", chosen=agent,
                       domain="support", session_id="day1")
    # the forward edge: this decision TOUCHED this business entity
    mem.record_touch(d, "order:1234", role="entity", detail={"by": agent})

# ---- question 1: what touched order:1234? (across every agent) ----------
print("everything that touched order:1234:")
for t in mem.touching("order:1234"):
    print(f"  - {t['by']}")

# ---- question 2: why is this belief held? (backward, cited) -------------
decision = mem.beliefs(predicate="decision")[0]
why = mem.why(decision["id"])
a = why["assertion"]
print(f"\nwhy({decision['id'][:16]}...):")
print(f"  statement:  {a['statement'][:70]}")
print(f"  confidence: {a['confidence']['value']} ({a['confidence']['method']})")
print(f"  origin:     {why['provenance']['origin_type']}")

# ---- question 3: both directions at once --------------------------------
prov = mem.provenance(decision["id"])
print(f"\nprovenance({decision['id'][:16]}...):")
print(f"  derived_from: {len(prov['derived_from'])} upstream evidence item(s)")
print(f"  touched:      {[t['target'] for t in prov['touched']]}")

# What you learned:
#   mem.record_touch(...)  -> forward edge: decision -> entity it affected
#   mem.touching(entity)   -> full cross-agent history of one entity
#   mem.why(id)            -> the cited reason a belief is held
#   mem.provenance(id)     -> the graph, both directions, one call
#
# That's the tour. examples/ has deeper end-to-end demos (real local LLM
# healing, autonomous promotion, A2A + healing in one run).
