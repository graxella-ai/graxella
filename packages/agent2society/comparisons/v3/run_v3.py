"""v3 benchmark driver: spin up servers, run both coordinators on N scenarios,
print reports + save JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from comparisons.v3.scenarios_v3 import SCENARIOS
from comparisons.v3.server_manager import ServerManager
from comparisons.v3.coordinators.baseline_supervisor import run_baseline_scenario
from comparisons.v3.coordinators.a2s_coordinator import (
    discover_agents,
    run_a2s_scenario,
)
from comparisons.v3.metrics_v3 import (
    RunMetricsV3,
    ComparisonReportV3,
    print_aggregate_summary,
)


def _serialize_metrics(m: RunMetricsV3) -> dict:
    """Round-trip a RunMetricsV3 to a JSON-friendly dict."""
    d = asdict(m)
    # _audit_field_hits is internal -- drop it
    d.pop("_audit_field_hits", None)
    return d


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scenarios", type=int, default=len(SCENARIOS),
                   help="Number of scenarios to run (default: all 12)")
    p.add_argument("--quick", action="store_true",
                   help="Use qwen2.5:0.5b for fast smoke runs")
    p.add_argument("--no-server-spawn", action="store_true",
                   help="Assume servers are already running on ports 5001-5007")
    p.add_argument("--output", type=str, default=None,
                   help="Output JSON path (default: comparisons/v3/results/<ts>.json)")
    args = p.parse_args()

    if args.quick:
        os.environ["V3_MODEL"] = "qwen2.5:0.5b"
        print(f"[run_v3] QUICK MODE: V3_MODEL=qwen2.5:0.5b", flush=True)
    else:
        os.environ.setdefault("V3_MODEL", "qwen2.5:7b")
    model = os.environ["V3_MODEL"]

    scenarios = SCENARIOS[: args.scenarios]
    print(f"[run_v3] running {len(scenarios)} scenarios with model={model}", flush=True)
    print(f"[run_v3] baseline: real LangGraph supervisor with Ollama + real A2A workers", flush=True)
    print(f"[run_v3] a2s    : agent2society Society with TF-IDF + same real A2A workers", flush=True)

    out_path = args.output or str(
        ROOT / "comparisons" / "v3" / "results" / f"v3_run_{int(time.time())}.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    cm = (
        ServerManager(startup_timeout=180.0)
        if not args.no_server_spawn
        else _NoOpContext()
    )
    baseline_runs: List[RunMetricsV3] = []
    a2s_runs: List[RunMetricsV3] = []
    overall_t0 = time.perf_counter()

    with cm as mgr:
        if mgr is None:
            # no-server-spawn path: build URLs ourselves
            from comparisons.v3.agents import AGENTS_V3
            agent_urls = {
                name: f"http://localhost:{cfg['port']}"
                for name, cfg in AGENTS_V3.items()
            }
        else:
            agent_urls = mgr.agent_urls()

        # Pre-discover agent cards once for the a2s runner (and time it)
        t0 = time.perf_counter()
        cards = discover_agents(agent_urls)
        discovery_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[run_v3] A2A discovery: {discovery_ms:.1f} ms ({len(cards)} cards)", flush=True)

        for idx, scenario in enumerate(scenarios, start=1):
            print(f"\n[run_v3] === scenario {idx}/{len(scenarios)}: {scenario['name']} ===", flush=True)

            print(f"[run_v3] running BASELINE ...", flush=True)
            try:
                bm = run_baseline_scenario(scenario, agent_urls)
            except Exception as exc:
                print(f"[run_v3] BASELINE ERROR: {exc}", flush=True)
                import traceback; traceback.print_exc()
                bm = RunMetricsV3(runner="baseline", scenario_name=scenario["name"])
                bm.dispatch_errors += 1
                bm.finalize()

            print(f"[run_v3] running AGENT2SOCIETY ...", flush=True)
            try:
                am = run_a2s_scenario(scenario, agent_urls, cards=cards)
            except Exception as exc:
                print(f"[run_v3] A2S ERROR: {exc}", flush=True)
                import traceback; traceback.print_exc()
                am = RunMetricsV3(runner="agent2society", scenario_name=scenario["name"])
                am.dispatch_errors += 1
                am.finalize()
            # carry the global discovery on the first scenario
            if idx == 1:
                am.a2a_discovery_ms = discovery_ms

            baseline_runs.append(bm)
            a2s_runs.append(am)
            ComparisonReportV3(bm, am).print_report()

    overall_ms = (time.perf_counter() - overall_t0) * 1000.0
    print_aggregate_summary(baseline_runs, a2s_runs)
    print(f"\n[run_v3] total elapsed: {overall_ms / 1000.0:.1f} s")

    payload = {
        "model": model,
        "n_scenarios": len(scenarios),
        "total_elapsed_ms": overall_ms,
        "baseline_runs": [_serialize_metrics(m) for m in baseline_runs],
        "a2s_runs": [_serialize_metrics(m) for m in a2s_runs],
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[run_v3] results saved to: {out_path}")
    return 0


class _NoOpContext:
    def __enter__(self): return None
    def __exit__(self, *a): return False


if __name__ == "__main__":
    sys.exit(main())
