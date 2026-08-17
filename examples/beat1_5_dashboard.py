"""Beat 1.5 dashboard demo -- boots the operator UI over a live runtime.

Run:
    /c/Users/Sridhar/anaconda3/python examples/beat1_5_dashboard.py

Then open http://127.0.0.1:8787/ in a browser. The page auto-refreshes
every 5s. You'll see:

  * Three seeded proposals: one AUTO_APPROVE, one wide-blast NEEDS_HUMAN,
    one compliance-floor AUTO_REJECT.
  * The Constitution violation from routing to the "frozen" writer agent.
  * The full tracer event stream from routing + gate + governance.

You can approve/reject the pending one from the dashboard buttons.
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


def build_runtime():
    workdir = Path(tempfile.mkdtemp(prefix="graxella-beat15-"))
    print(f"[workdir] {workdir}")

    memory = Memory.sqlite(db_path=str(workdir / "mnema.db"), agent_id="pipeline_v1")
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

    # Seed activity so the dashboard has something to show.
    for task in ["find peer-reviewed sources on transformer scaling laws",
                 "compute the mean of 1,2,3,4,5,6,7",
                 "write a two-paragraph summary of the findings"]:
        wrapped.route(task)

    # Seed three scored proposals -- one of each decision path.
    gate.propose("route.tag_add",
                 {"agent": "calculator", "add_terms": ["compute mean", "average"]},
                 blast_radius="narrow",
                 objectives=ObjectiveScores(cost_usd=0.02, latency_ms=120,
                                            quality=0.95, compliance=1.0))
    gate.propose("rule.new",
                 {"pattern": "all outbound emails require legal review"},
                 blast_radius="wide",
                 objectives=ObjectiveScores(cost_usd=0.02, latency_ms=120,
                                            quality=0.95, compliance=1.0))
    gate.propose("skill.new",
                 {"agent": "writer", "skill": "unreviewed_draft"},
                 blast_radius="narrow",
                 objectives=ObjectiveScores(cost_usd=0.02, latency_ms=120,
                                            quality=0.95, compliance=0.5))
    # Kick off policy evaluations so their statuses show in the UI.
    for p in list(gate.pending()):
        gate.auto_evaluate(p.id, by="policy")

    return wrapped


def main() -> None:
    wrapped = build_runtime()
    app = create_app(
        tracer=wrapped.tracer, memory=wrapped.memory,
        society=wrapped.society, gate=wrapped.gate,
        constitution=wrapped.constitution,
    )
    try:
        import uvicorn  # type: ignore
    except ImportError:
        raise SystemExit("uvicorn not installed. `pip install uvicorn` and re-run.")

    print("\n[serve] http://127.0.0.1:8787/           <- dashboard")
    print("[serve] http://127.0.0.1:8787/docs        <- Swagger")
    print("[serve] Ctrl+C to stop\n")
    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="warning")


if __name__ == "__main__":
    main()
