"""Phase 2 exit demo — the Evidence Loop, end to end, one script.

Run from the repo root:   uv run python showcase/06_evidence_loop.py

Three acts, no LLM required (a real one slots in wherever a stub sits):

  ACT 1  a multi-hop workflow runs through the trajectory runtime —
         typed handoff, per-hop decisions + outcomes, chain in the ledger
  ACT 2  a tool drifts: healed ONCE via a (stub) healer, proposed
         through the Evidence Gate, human-approved, promoted — the
         second drift heals with ZERO healer calls
  ACT 3  the ledger answers: gate.why() citations, token accounting,
         cited tool trust — every number traceable to assertions
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import graxella
from graxella.beliefs import Memory
from graxella.gate.evidence import EvidenceGate, pending_from_ledger
from graxella.healing import ToolInterceptor, TransformRecipe, tool_trust
from graxella.rulebook import Rulebook

work = Path(tempfile.mkdtemp(prefix="graxella-demo-"))
memory = Memory.sqlite(str(work / "mnema.db"), agent_id="demo",
                       namespace="refunds")

print("=" * 64)
print("ACT 1 — multi-hop trajectory, every hop audited")
print("=" * 64)


def triage(payload):
    """decide refund eligibility for billing complaints and orders"""
    return {"result": "eligible for refund. HANDOFF: draft_email :: write a "
                      f"friendly apology email response to the customer about {payload}"}


def draft_email(payload):
    """write a friendly apology email response to the customer"""
    return {"result": f"Dear customer — {payload}. With apologies, Support."}


app = graxella.mesh([triage, draft_email], memory=memory,
                    store_path=str(work / "routes.jsonl"),
                    domain="refunds", model_id="stub-llm")
t = app.run_trajectory("billing refund for order 1234, arrived damaged")
print(f"status={t.status}  hops={[h.agent for h in t.hops]}")
print(f"final: {t.final_response[:70]}...")
print(f"chain ledger object: {t.assertion_id}  "
      f"(cites {len(t.hops)} hop decisions)")

print()
print("=" * 64)
print("ACT 2 — fail once, learn forever")
print("=" * 64)


def weather_v1(args):
    raise RuntimeError("HTTP_410_GONE: schema deprecated, use weather.v2")


def weather_v2(args):
    return f"forecast for {args['location']}: sunny, 27C"


def stub_healer(tool, args, error):     # a real LLM slots in here — once
    print(f"  [healer invoked ONCE for {tool}: proposing city->location]")
    return TransformRecipe(field_map={"city": "location"})


rulebook = Rulebook(path=work / "rulebook.json")
gate = EvidenceGate(memory)
tool = ToolInterceptor(weather_v1, tool_name="get_weather", memory=memory,
                       rulebook=rulebook, gate=gate, fallback=weather_v2,
                       healer=stub_healer, domain="refunds",
                       model_id="stub-llm")

print("first drift:", tool({"city": "Bengaluru"}))
pend = pending_from_ledger(memory)[0]
print(f"proposal in review queue: {pend['proposal_id']}  ({pend['reason'][:48]}...)")

gate.approve(pend["proposal_id"], by="operator:sridhar", note="rename verified")
_, approved = gate.decide(
    TransformRecipe(field_map={"city": "location"}).to_proposal(
        domain="refunds", tool="get_weather", origin="healer:axon-fabric",
        model_id="stub-llm"))
rule = rulebook.promote(approved, domain="refunds")
print(f"promoted: {rule.id}  status={rule.spec_status}  "
      f"citations={len(rule.citations)}")

print("second drift:", tool({"city": "Chennai"}))
print(f"healer calls total: {tool.healer_calls}   <-- fail once, learn forever")

print()
print("=" * 64)
print("ACT 3 — the ledger answers")
print("=" * 64)
print("-- gate.why(promotion) " + "-" * 30)
print(gate.why(pend["proposal_id"]))
print("-- token / outcome accounting (from the ledger alone) " + "-" * 6)
stats = memory.outcome_stats()["total"]
print(f"outcomes={stats['count']}  ok_rate={stats['ok_rate']}  "
      f"avg_latency_ms={stats['avg_latency_ms']}")
print("-- cited tool trust " + "-" * 34)
for name, tr in tool_trust(memory, domain="refunds").items():
    print(f"{name}: score={tr.score}  ({tr.successes} ok / {tr.failures} fail, "
          f"{len(tr.citations)} citations)")
print()
print(f"workdir: {work}")
