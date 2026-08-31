"""Showcase 02 -- the full agentic-orchestration story.

PROVES the six things a graxella runtime does:
  1. wrap  -- one-line instrument() over an existing app
  2. route -- deterministic TF-IDF routing to the best agent+skill
  3. dispatch -- real callable execution via LocalTransport
  4. constitution -- invariant violations become tracer events (never block)
  5. gate -- multi-objective scoring drives auto-approve / needs-human / auto-reject
  6. why -- cross-source provenance join across mnema + tracer

Run:
    /c/Users/Sridhar/anaconda3/python showcase/02_full_runtime.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "graxella"))

from graxella import (Constitution, GatePolicy, Memory, ObjectiveScores,
                      PromotionGate, Society, UnifiedTracer, instrument)


class MockLangGraphApp:
    def invoke(self, state: dict, config: dict | None = None) -> dict:
        return {"input": state.get("input"), "output": f"processed({state.get('input')})"}


def research_agent(payload: Any) -> dict:  return {"result": f"researched: {payload}"}
def calculate_agent(payload: Any) -> dict: return {"result": f"computed: {payload}"}
def write_agent(payload: Any) -> dict:     return {"result": f"drafted: {payload}"}


def section(title: str) -> None:
    print(f"\n{'-' * 76}\n{title}\n{'-' * 76}")


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="graxella-showcase-02-"))
    print(f"[setup] workdir: {workdir}")

    # --------------------------------------------------------------- (1) wrap
    section("(1) WRAP -- one-line instrument()")
    memory = Memory.sqlite(db_path=str(workdir / "mnema.db"), agent_id="showcase")
    society = Society(store_path=str(workdir / "routes.jsonl"))
    society.add("researcher", research_agent, skills=["research literature", "find sources"])
    society.add("calculator", calculate_agent, skills=["arithmetic", "statistics", "compute"])
    society.add("writer",     write_agent,     skills=["draft prose", "summarise findings"])

    tracer = UnifiedTracer.default()

    policy = GatePolicy(
        weights={"quality": 0.4, "compliance": 0.3, "cost": 0.2, "latency": 0.1},
        cost_reference=0.10, latency_reference=500.0,
        compliance_floor=0.9, auto_approve=0.85, needs_human_min=0.5,
    )
    gate = PromotionGate(threshold=0.85, require_human=False, policy=policy)

    constitution = Constitution.from_dict({
        "version": "1.0",
        "invariants": [{
            "name": "delegate.no_frozen_agents",
            "applies_to": "delegate", "severity": "error",
            "predicate": {"type": "object",
                          "properties": {"chosen_agent": {"not": {"const": "writer"}}}},
        }],
    })

    wrapped = instrument(MockLangGraphApp(), memory=memory, society=society,
                         tracer=tracer, gate=gate, constitution=constitution)
    print(f"  wrap ok. agents registered: {wrapped.society.agents()}")

    # ------------------------------------------------------ (2)+(3) route+dispatch
    section("(2)+(3) ROUTE + DISPATCH -- 4 tasks, deterministic pick")
    tasks = [
        "find peer-reviewed sources on transformer scaling laws",
        "compute the mean of these seven numbers: 1,2,3,4,5,6,7",
        "write a two-paragraph summary of the findings",  # will trip constitution
        "arithmetic write summary",                        # ambiguous
    ]
    decision_ids = []
    for t in tasks:
        result, aid = wrapped.route(t)
        decision_ids.append(aid)
        print(f"  {t[:52]:<52}")
        print(f"      -> {result.chosen_agent}::{result.chosen_skill}"
              f" score={result.score:.3f} margin={result.margin:.3f} flags={list(result.flags)}")
        print(f"      response: {result.response[:60]!r}")

    # ----------------------------------------------------- (4) constitution
    section("(4) CONSTITUTION -- invariant surfaced (not blocked)")
    viols = tracer.events(event_type="governance.constitution_violation")
    print(f"  {len(viols)} violation(s) recorded")
    for v in viols:
        p = v.payload
        print(f"    [{p['severity']}] {p['name']}: {p['detail']}")
        print(f"       linked to decision: {p['decision_id']}")

    # ------------------------------------------------------- (4b) outcomes
    section("(4b) OUTCOMES -- reward signal fed back into memory")
    for aid in decision_ids[:2]:
        oid = memory.record_outcome(decision_id=aid, ok=True, score=0.9,
                                    cost_tokens=180, latency_ms=340.0)
        print(f"  outcome {oid} recorded for decision {aid}")

    # ------------------------------------------------------------ (5) gate
    section("(5) GATE -- three scored proposals, three different decisions")
    proposals = [
        ("route.tag_add",
         {"agent": "calculator", "add_terms": ["compute mean", "average"]},
         "narrow",
         ObjectiveScores(cost_usd=0.02, latency_ms=120, quality=0.95, compliance=1.0)),
        ("rule.new",
         {"pattern": "outbound emails require legal review"},
         "wide",
         ObjectiveScores(cost_usd=0.02, latency_ms=120, quality=0.95, compliance=1.0)),
        ("skill.new",
         {"agent": "writer", "skill": "unreviewed_draft"},
         "narrow",
         ObjectiveScores(cost_usd=0.02, latency_ms=120, quality=0.95, compliance=0.5)),
    ]
    for kind, payload, blast, obj in proposals:
        p = gate.propose(kind, payload, blast_radius=blast, objectives=obj)
        decision, after = gate.auto_evaluate(p.id)
        print(f"  #{after.id} {after.kind:<14} blast={after.blast_radius:<7} "
              f"scalar={after.score:.3f} compliance={obj.compliance:.2f} "
              f"-> {decision.value.upper():<13} (status={after.status.value})")

    # -------------------------------------------------- (6) why -- provenance
    section("(6) WHY -- cross-source provenance join")
    joint = wrapped.why(decision_ids[0])
    print(f"  tracer chain events: {len(joint['tracer_chain'])}")
    mnema = joint.get("mnema") or {}
    if isinstance(mnema, dict) and "error" not in mnema:
        print(f"  mnema keys: {list(mnema.keys())}")

    # ----------------------------------------------- summary tracer snapshot
    section("TRACER -- unified stream across all sources")
    for e in tracer.events(limit=15):
        p = e.payload
        head = f"  seq={e.seq:>2} {e.source:<12} {e.event_type:<32}"
        if e.event_type == "route":
            print(f"{head} task={(p.get('task') or '')[:35]!r} -> {p.get('chosen_agent')}")
        elif e.event_type.startswith("proposal."):
            print(f"{head} id={p.get('id')} kind={p.get('kind')}")
        elif e.event_type.startswith("governance."):
            print(f"{head} name={p.get('name')} severity={p.get('severity')}")
        elif e.event_type == "outcome":
            print(f"{head} decision={p.get('decision_id')} ok={p.get('ok')}")
        else:
            print(f"{head}")

    print("\nSHOWCASE COMPLETE.")


if __name__ == "__main__":
    main()
