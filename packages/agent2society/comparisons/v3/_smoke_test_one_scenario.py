"""Phase 3/4 smoke: run ONE scenario end-to-end on baseline + a2s with qwen2.5:0.5b."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path: sys.path.insert(0, str(ROOT / "src"))

os.environ["V3_MODEL"] = "qwen2.5:0.5b"

from comparisons.v3.server_manager import ServerManager
from comparisons.v3.scenarios_v3 import SCENARIOS
from comparisons.v3.coordinators.baseline_supervisor import run_baseline_scenario
from comparisons.v3.coordinators.a2s_coordinator import run_a2s_scenario, discover_agents
from comparisons.v3.metrics_v3 import ComparisonReportV3


def main() -> int:
    # Pick a small scenario with a boundary trap and dependencies
    scenario = SCENARIOS[7]  # Restricted List Compliance Audit (5 tasks)
    print(f"[smoke_one] scenario = {scenario['name']}")
    with ServerManager(startup_timeout=180.0) as mgr:
        urls = mgr.agent_urls()
        cards = discover_agents(urls)
        t0 = time.perf_counter()
        print("[smoke_one] running baseline ...")
        bm = run_baseline_scenario(scenario, urls)
        print(f"[smoke_one] baseline: {(time.perf_counter()-t0)*1000:.0f} ms")
        t0 = time.perf_counter()
        print("[smoke_one] running a2s ...")
        am = run_a2s_scenario(scenario, urls, cards=cards)
        print(f"[smoke_one] a2s: {(time.perf_counter()-t0)*1000:.0f} ms")
    ComparisonReportV3(bm, am).print_report()
    print()
    print(f"[smoke_one] baseline tokens (in/out): "
          f"{bm.coordination_input_tokens}/{bm.coordination_output_tokens} "
          f"coord + {bm.execution_input_tokens}/{bm.execution_output_tokens} exec")
    print(f"[smoke_one] a2s tokens (in/out):      "
          f"{am.coordination_input_tokens}/{am.coordination_output_tokens} "
          f"coord + {am.execution_input_tokens}/{am.execution_output_tokens} exec")
    if bm.total_tokens == 0:
        print("[smoke_one] FAIL: baseline reported zero tokens"); return 1
    if am.execution_tokens_total == 0:
        print("[smoke_one] FAIL: a2s execution tokens = 0"); return 1
    print("[smoke_one] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
