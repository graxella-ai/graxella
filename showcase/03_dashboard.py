"""Showcase 03 -- operator dashboard over a live runtime.

PROVES: an operator can watch the runtime, approve/reject pending
proposals, and see constitution violations + tracer events -- all from a
browser -- with zero build step (single-file HTML + vanilla JS).

Run:
    /c/Users/Sridhar/anaconda3/python showcase/03_dashboard.py

Then open in your browser:
    http://127.0.0.1:8787/          <- operator dashboard
    http://127.0.0.1:8787/docs      <- Swagger auto-docs

Ctrl+C to stop.
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
from graxella.api import create_app


class MockApp:
    def invoke(self, state: dict, config: dict | None = None) -> dict:
        return {"input": state.get("input"), "output": f"processed({state.get('input')})"}


def research(payload: Any) -> dict:  return {"result": f"researched: {payload}"}
def calculate(payload: Any) -> dict: return {"result": f"computed: {payload}"}
def write(payload: Any) -> dict:     return {"result": f"drafted: {payload}"}


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="graxella-showcase-03-"))
    print(f"[setup] workdir: {workdir}")

    memory = Memory.sqlite(db_path=str(workdir / "mnema.db"), agent_id="showcase")
    society = Society(store_path=str(workdir / "routes.jsonl"))
    society.add("researcher", research, skills=["research literature", "find sources"])
    society.add("calculator", calculate, skills=["arithmetic", "statistics", "compute"])
    society.add("writer",     write,     skills=["draft prose", "summarise findings"])

    tracer = UnifiedTracer.default()
    policy = GatePolicy(
        weights={"quality": 0.4, "compliance": 0.3, "cost": 0.2, "latency": 0.1},
        cost_reference=0.10, latency_reference=500.0,
        compliance_floor=0.9, auto_approve=0.85, needs_human_min=0.5,
    )
    gate = PromotionGate(threshold=0.85, require_human=True, policy=policy)
    constitution = Constitution.from_dict({
        "version": "1.0",
        "invariants": [{
            "name": "delegate.no_frozen_agents",
            "applies_to": "delegate", "severity": "error",
            "predicate": {"type": "object",
                          "properties": {"chosen_agent": {"not": {"const": "writer"}}}},
        }],
    })

    wrapped = instrument(MockApp(), memory=memory, society=society,
                         tracer=tracer, gate=gate, constitution=constitution)

    # Seed activity so the dashboard has something to show on first paint.
    for task in ["find peer-reviewed sources on transformer scaling laws",
                 "compute the mean of 1,2,3,4,5,6,7",
                 "write a two-paragraph summary of the findings"]:
        wrapped.route(task)

    for kind, payload, blast, obj in [
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
    ]:
        gate.propose(kind, payload, blast_radius=blast, objectives=obj)
    for p in list(gate.pending()):
        gate.auto_evaluate(p.id, by="policy")

    app = create_app(tracer=tracer, memory=memory, society=society,
                     gate=gate, constitution=constitution)

    try:
        import uvicorn  # type: ignore
    except ImportError:
        raise SystemExit("uvicorn not installed. `pip install uvicorn` and re-run.")

    print("\n" + "=" * 72)
    print("  DASHBOARD:  http://127.0.0.1:8787/")
    print("  SWAGGER:    http://127.0.0.1:8787/docs")
    print("  Ctrl+C to stop.")
    print("=" * 72 + "\n")
    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="warning")


if __name__ == "__main__":
    main()
