"""Top-level orchestrator for the v2 multi-agent routing benchmark.

Runs every scenario through both runners, computes determinism by
re-running agent2society three times with the same seed, prints a
rich per-scenario report, the headline aggregate summary, and writes
a full JSON dump to comparisons/v2/results/.

Usage:
    python comparisons/v2/run_v2.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Make repo root importable as a package root for `from comparisons.v2....`
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from comparisons.v2 import (
    baseline_langgraph_v2 as baseline,
    with_agent2society_v2 as a2s,
)
from comparisons.v2.metrics_v2 import (
    ComparisonReportV2,
    RunMetricsV2,
    print_aggregate_summary,
)
from comparisons.v2.scenarios_v2 import SCENARIOS


# ---------------------------------------------------------------------------
# Determinism: re-run a2s 3 times and count identical (agent, skill) pairs.
# ---------------------------------------------------------------------------

def compute_determinism(scenario: Dict[str, Any]) -> float:
    runs: List[List[tuple]] = []
    for seed in (101, 102, 103):
        m = a2s.run_scenario(scenario, seed=seed)
        runs.append([(r["agent"], r["skill"]) for r in m.task_records])
    if not runs or not runs[0]:
        return 1.0
    n = len(runs[0])
    if any(len(r) != n for r in runs):
        return 0.0
    identical = sum(
        1 for i in range(n) if runs[0][i] == runs[1][i] == runs[2][i]
    )
    return identical / n if n else 1.0


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------

def metrics_to_dict(m: RunMetricsV2) -> Dict[str, Any]:
    keys = [
        "runner",
        "scenario_name",
        "coordination_input_tokens",
        "coordination_output_tokens",
        "coordination_tokens_total",
        "execution_input_tokens",
        "execution_output_tokens",
        "execution_tokens_total",
        "total_tokens",
        "cost_usd_gpt4o_mini",
        "cost_usd_gpt4o",
        "cost_usd_claude_opus_4",
        "latency_per_routing_ms",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
        "latency_max_ms",
        "time_to_first_dispatch_ms",
        "total_wall_time_ms",
        "throughput_tasks_per_sec",
        "coordination_calls_total",
        "coordination_calls_per_task",
        "context_tokens_growth",
        "avg_context_tokens_first_half",
        "avg_context_tokens_second_half",
        "flags_low_margin_count",
        "flags_ood_count",
        "flags_vector_ambiguity_count",
        "confidence_distribution",
        "margin_distribution",
        "runner_up_capture_rate",
        "conformance_violations_caught",
        "low_confidence_hook_fired_count",
        "conflict_hook_fired_count",
        "capability_drift_hook_fired_count",
        "decisions_with_explanation",
        "decisions_with_alternatives_recorded",
        "decisions_with_runner_up_reason",
        "audit_completeness_score",
        "dispatch_errors",
        "dispatch_retries",
        "cold_start_ms",
        "peak_memory_mb",
        "determinism_score",
        "num_routing_decisions",
        "correct_routings",
        "routing_accuracy",
        "has_explanations",
        "has_conformance",
        "has_governance_hooks",
    ]
    out = {k: getattr(m, k) for k in keys}
    out["task_records"] = m.task_records
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    W = 84
    print()
    print("=" * W)
    print("  agent2society vs LangGraph Supervisor - v2 BENCHMARK".center(W))
    print(
        "  (shared TF-IDF brain on both sides; we measure ONLY coord overhead)".center(
            W
        )
    )
    print("=" * W)

    print()
    print("  Cold-start measurements (instantiate from scratch):")
    cold_base = baseline.measure_cold_start()
    cold_a2s = a2s.measure_cold_start()
    print(f"    LangGraph baseline : {cold_base:8.2f} ms")
    print(f"    agent2society      : {cold_a2s:8.2f} ms")

    baseline_runs: List[RunMetricsV2] = []
    a2s_runs: List[RunMetricsV2] = []

    for scenario in SCENARIOS:
        print()
        print(f"  >>> Running scenario: {scenario['name']}")
        b = baseline.run_scenario(scenario, seed=1337)
        baseline_runs.append(b)
        a = a2s.run_scenario(scenario, seed=1337)
        # determinism: re-run two more times under different seeds
        a.determinism_score = compute_determinism(scenario)
        a2s_runs.append(a)
        ComparisonReportV2(b, a).print_report()

    print_aggregate_summary(baseline_runs, a2s_runs)

    # ---- Save JSON ----
    out_dir = HERE / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"v2_comparison_{ts}.json"
    payload = {
        "generated_at": ts,
        "cold_start_ms": {
            "baseline": cold_base,
            "agent2society": cold_a2s,
        },
        "scenarios": [
            {
                "name": s["name"],
                "baseline": metrics_to_dict(b),
                "agent2society": metrics_to_dict(a),
            }
            for s, b, a in zip(SCENARIOS, baseline_runs, a2s_runs)
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"  JSON saved to: {out_path}")
    print()


if __name__ == "__main__":
    main()
