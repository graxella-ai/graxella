"""Beat 1 end-to-end demo — the one-line ``instrument()`` wrap in action.

Zero external services: no Ollama, no LangGraph install required. The demo
uses a MockLangGraphApp so anyone can run it out of the box.

What it shows:
  1. ``instrument(app, memory=..., society=...)`` composes the runtime.
  2. Registering fake "agents" (plain callables) with the Society.
  3. Routing a task -> deterministic Society decision + Memory write
     + UnifiedTracer event, all in one call.
  4. A borderline-ambiguous task triggering the LOW_MARGIN governance
     detector -- surfaced through the tracer, never auto-corrected.
  5. Proposing + approving a learned strategy through the PromotionGate.
  6. ``why(assertion_id)`` joining the tracer chain with mnema's
     provenance view.

Run:
    /c/Users/Sridhar/anaconda3/python examples/beat1_instrument_demo.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

# Make `graxella` importable when running this file directly from a
# source checkout (before ``pip install -e .``).
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "graxella"))

import graxella
from graxella import (
    Constitution,
    GatePolicy,
    Memory,
    ObjectiveScores,
    PromotionGate,
    Society,
    UnifiedTracer,
    instrument,
)


# ---------- 1. A mock LangGraph app -----------------------------------------
class MockLangGraphApp:
    """Minimal stand-in for a LangGraph StateGraph.compile() result.

    Real LangGraph apps expose ``.invoke(state, config)``. We match that
    shape so ``instrument(...)`` treats us identically.
    """

    def invoke(self, state: dict, config: dict | None = None) -> dict:
        # Simulate a chain: read input, produce an output.
        return {"input": state.get("input"), "output": f"processed({state.get('input')})"}


# ---------- 2. Three fake agents --------------------------------------------
def research_agent(payload: Any) -> dict:
    return {"agent": "research", "result": f"researched: {payload}"}


def calculate_agent(payload: Any) -> dict:
    return {"agent": "calculate", "result": f"computed: {payload}"}


def write_agent(payload: Any) -> dict:
    return {"agent": "write", "result": f"drafted: {payload}"}


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="graxella-beat1-"))
    print(f"[workdir] {workdir}\n")

    # ---------- 3. Compose the runtime primitives ----------------------------
    memory = Memory.sqlite(
        db_path=str(workdir / "mnema.db"),
        agent_id="pipeline_v1",
    )

    society = Society(store_path=str(workdir / "routes.jsonl"))
    society.add("researcher", research_agent, skills=["research literature", "find sources"])
    society.add("calculator", calculate_agent, skills=["arithmetic", "statistics", "compute"])
    society.add("writer",     write_agent,     skills=["draft prose", "summarise findings"])

    tracer = UnifiedTracer.default()
    # Multi-objective gate: quality/compliance heavy, cost/latency light.
    # Narrow blast + high scalar auto-approves; wide blast always needs
    # human; compliance < 0.9 is a hard reject.
    policy = GatePolicy(
        weights={"quality": 0.4, "compliance": 0.3, "cost": 0.2, "latency": 0.1},
        cost_reference=0.10, latency_reference=500.0,
        compliance_floor=0.9, auto_approve=0.85, needs_human_min=0.5,
    )
    gate = PromotionGate(threshold=0.85, require_human=False, policy=policy)
    # A real constitution: the runtime must NOT delegate to `writer`
    # (perhaps it's under a compliance freeze), and every delegate
    # decision must carry a non-empty response.
    constitution = Constitution.from_dict({
        "version": "1.0",
        "invariants": [
            {
                "name": "delegate.no_frozen_agents",
                "applies_to": "delegate",
                "severity": "error",
                "predicate": {
                    "type": "object",
                    "properties": {"chosen_agent": {"not": {"const": "writer"}}},
                },
            },
            {
                "name": "delegate.response_present",
                "applies_to": "delegate",
                "severity": "warning",
                "predicate": {
                    "type": "object",
                    "properties": {"response": {"type": "string", "minLength": 1}},
                },
            },
        ],
    })

    app = MockLangGraphApp()
    wrapped = instrument(
        app,
        memory=memory,
        society=society,
        tracer=tracer,
        gate=gate,
        constitution=constitution,
    )
    print(f"[wrap] instrumented: {type(wrapped).__name__} over {type(app).__name__}")
    print(f"[wrap] agents registered: {[a for a in society._mesh.agents()]}\n")

    # ---------- 4. Pass-through invoke works transparently ------------------
    passthrough = wrapped.invoke({"input": "hello"})
    print(f"[invoke] pass-through result: {passthrough}\n")

    # ---------- 5. Route several tasks + record decisions -------------------
    tasks = [
        "find peer-reviewed sources on transformer scaling laws",
        "compute the mean of these seven numbers: 1,2,3,4,5,6,7",
        "write a two-paragraph summary of the findings",
        "arithmetic write summary",  # deliberately ambiguous -- may trip LOW_MARGIN
    ]
    decision_ids: list[str] = []
    for task in tasks:
        result, aid = wrapped.route(task)
        decision_ids.append(aid)
        print(f"[route] {task!r}")
        print(f"        chose {result.chosen_agent}::{result.chosen_skill} "
              f"score={result.score:.3f} margin={result.margin:.3f} flags={result.flags}")

    # ---------- 6. Simulate outcomes ----------------------------------------
    print("\n[outcomes]")
    for aid in decision_ids[:2]:
        oid = memory.record_outcome(
            decision_id=aid, ok=True, score=0.92,
            cost_tokens=180, latency_ms=340.0,
        )
        print(f"        recorded outcome {oid} for decision {aid}")

    # ---------- 7. Gate: three scored proposals, policy-driven decisions ---
    print("\n[gate] proposing scored strategies (multi-objective)...")
    # (a) Strong proposal on narrow blast: should AUTO_APPROVE.
    good = gate.propose(
        kind="route.tag_add",
        payload={"agent": "calculator", "skill": "arithmetic",
                 "add_terms": ["compute mean", "average"]},
        blast_radius="narrow",
        objectives=ObjectiveScores(cost_usd=0.02, latency_ms=120,
                                   quality=0.95, compliance=1.0),
    )
    # (b) Same strong shape but wide blast: forced to NEEDS_HUMAN.
    wide = gate.propose(
        kind="rule.new",
        payload={"pattern": "all outbound emails require legal review"},
        blast_radius="wide",
        objectives=ObjectiveScores(cost_usd=0.02, latency_ms=120,
                                   quality=0.95, compliance=1.0),
    )
    # (c) Compliance floor breach: AUTO_REJECT regardless of quality.
    bad = gate.propose(
        kind="skill.new",
        payload={"agent": "writer", "skill": "unreviewed_draft"},
        blast_radius="narrow",
        objectives=ObjectiveScores(cost_usd=0.02, latency_ms=120,
                                   quality=0.95, compliance=0.5),
    )

    for prop in (good, wide, bad):
        decision, after = gate.auto_evaluate(prop.id)
        obj = prop.objectives
        print(f"        id={after.id} kind={after.kind:<14} "
              f"scalar={after.score:.3f} blast={after.blast_radius:<7} "
              f"compliance={obj.compliance:.2f} -> {decision.value} "
              f"(status={after.status.value})")

    # Activate the auto-approved one so it becomes live policy.
    if good.status.value == "approved":
        active = gate.activate(good.id, by="operator@graxella")
        print(f"        activated: id={active.id} status={active.status.value}")

    # ---------- 8. Constitution violations --------------------------------
    viols = tracer.events(event_type="governance.constitution_violation")
    print(f"\n[constitution] {len(viols)} violation(s) recorded")
    for evt in viols:
        p = evt.payload
        print(f"        {p.get('severity','?'):<7} {p.get('name'):<32} {p.get('detail')}")

    # ---------- 9. Unified tracer -------------------------------------------
    print("\n[tracer] event stream (last 10):")
    for evt in tracer.events(limit=10):
        p = evt.payload
        head = f"seq={evt.seq:>3} {evt.source:<10} {evt.event_type:<28}"
        if evt.event_type == "route":
            print(f"  {head} task={p.get('task', '')[:40]!r} -> {p.get('chosen_agent')}")
        elif evt.event_type == "decision":
            print(f"  {head} {p.get('chosen')} :: {p.get('rationale', '')[:40]}")
        elif evt.event_type == "outcome":
            print(f"  {head} decision={p.get('decision_id')} ok={p.get('ok')}")
        elif evt.event_type.startswith("proposal."):
            print(f"  {head} id={p.get('id')} kind={p.get('kind')}")
        elif evt.event_type.startswith("governance."):
            print(f"  {head} {p!r}")
        else:
            print(f"  {head} {p!r}")

    # ---------- 9. Cross-source why -----------------------------------------
    print("\n[why] joint provenance for first decision:")
    joint = wrapped.why(decision_ids[0])
    print(f"        tracer_chain events: {len(joint['tracer_chain'])}")
    mnema_view = joint.get("mnema") or {}
    if isinstance(mnema_view, dict) and "error" not in mnema_view:
        print(f"        mnema keys: {list(mnema_view.keys())}")
    else:
        print(f"        mnema view: {mnema_view}")

    print("\nOK -- Beat 1 wrap end-to-end works.")


if __name__ == "__main__":
    main()
