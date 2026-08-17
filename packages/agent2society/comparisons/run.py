"""Main benchmark runner.

Usage:
    python comparisons/run.py

Runs each scenario through both the LangGraph baseline and agent2society,
prints side-by-side comparison reports, and saves JSON results.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import List

# Make sure the src/ editable package and comparisons/ are importable
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from comparisons import baseline_langgraph, with_agent2society
from comparisons.metrics import ComparisonReport, RunMetrics, print_aggregate_summary
from comparisons.scenarios import SCENARIOS

W = 72


def _banner(text: str) -> None:
    print()
    print("#" * W)
    print(f"#  {text}")
    print("#" * W)


def _section(text: str) -> None:
    print()
    print(f">>> {text}")
    print("-" * W)


def main() -> None:
    _banner("agent2society vs LangGraph Supervisor  --  Routing Benchmark")
    print(f"  Scenarios : {len(SCENARIOS)}")
    print(f"  Run date  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Package   : agent2society v0.5.3")

    baseline_runs: List[RunMetrics] = []
    a2s_runs: List[RunMetrics] = []

    for i, scenario in enumerate(SCENARIOS, 1):
        _section(f"Scenario {i}/{len(SCENARIOS)}: {scenario['name']}")

        # --- Baseline ---
        print(f"  [1/2] Running LangGraph Baseline...", end="", flush=True)
        b_metrics = baseline_langgraph.run_scenario(scenario)
        print(f" done  ({b_metrics.elapsed_ms:.0f} ms)")

        # --- agent2society ---
        print(f"  [2/2] Running agent2society ...", end="", flush=True)
        a_metrics = with_agent2society.run_scenario(scenario)
        print(f" done  ({a_metrics.elapsed_ms:.0f} ms)")

        baseline_runs.append(b_metrics)
        a2s_runs.append(a_metrics)

        # Per-scenario comparison
        report = ComparisonReport(b_metrics, a_metrics)
        report.print_report()

    # Aggregate summary
    _banner("AGGREGATE SUMMARY ACROSS ALL SCENARIOS")
    print_aggregate_summary(baseline_runs, a2s_runs)

    # Save JSON results
    results_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "results"
    )
    os.makedirs(results_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(results_dir, f"comparison_{timestamp}.json")

    all_results = {
        "run_date": datetime.now().isoformat(),
        "num_scenarios": len(SCENARIOS),
        "aggregate": {
            "total_baseline_coordination_tokens": sum(r.coordination_tokens for r in baseline_runs),
            "total_a2s_coordination_tokens": sum(r.coordination_tokens for r in a2s_runs),
            "total_baseline_tokens": sum(r.total_tokens for r in baseline_runs),
            "total_a2s_tokens": sum(r.total_tokens for r in a2s_runs),
            "avg_baseline_routing_accuracy": (
                sum(r.routing_accuracy for r in baseline_runs) / len(baseline_runs)
            ),
            "avg_a2s_routing_accuracy": (
                sum(r.routing_accuracy for r in a2s_runs) / len(a2s_runs)
            ),
        },
        "scenarios": [
            {
                "name": b.scenario_name,
                "baseline": {
                    "coordination_tokens": b.coordination_tokens,
                    "execution_tokens": b.execution_tokens,
                    "total_tokens": b.total_tokens,
                    "routing_accuracy": b.routing_accuracy,
                    "elapsed_ms": b.elapsed_ms,
                    "has_explanations": b.has_explanations,
                    "has_conformance": b.has_conformance,
                    "has_governance_hooks": b.has_governance_hooks,
                    "task_records": b.task_records,
                },
                "agent2society": {
                    "coordination_tokens": a.coordination_tokens,
                    "execution_tokens": a.execution_tokens,
                    "total_tokens": a.total_tokens,
                    "routing_accuracy": a.routing_accuracy,
                    "elapsed_ms": a.elapsed_ms,
                    "has_explanations": a.has_explanations,
                    "has_conformance": a.has_conformance,
                    "has_governance_hooks": a.has_governance_hooks,
                    "task_records": a.task_records,
                },
            }
            for b, a in zip(baseline_runs, a2s_runs)
        ],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    print(f"\n  Results saved to: {out_path}")
    print()


if __name__ == "__main__":
    main()
