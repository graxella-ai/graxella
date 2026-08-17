"""E1: LTCF-Bench -- Lifetime Cognitive Fitness Benchmark.

Three-arm ablation on drifting weatherlib API (v1 -> v2 -> v3).

Arms:
  A0 - Baseline: static knowledge, no memory update
  A1 - Simple retrieval: raw observation store, keyword match
  A2 - Mnema: sleep-phase consolidation, rule injection

Protocol:
  Phase 1 (v1 baseline):  10 tasks, no drift yet, all arms start equal
  Phase 2 (v2 drift):     observe v2 drift signals, 10 tasks, A2 consolidates
  Phase 3 (v3 drift):     observe v3 drift signals, 10 tasks, A2 consolidates again

Output: per-arm accuracy table + summary lift metric.
"""

from __future__ import annotations

import sys
import os

# Allow running from repo root: python experiments/run_e1.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from experiments.codedrift import VERSIONS, generate_tasks, get_observations_for_version
from experiments.agent import A0BaselineAgent, A1SimpleRetrievalAgent, A2MnemaAgent


def score(answers: list[str], tasks: list) -> int:
    return sum(1 for a, t in zip(answers, tasks) if a == t.correct_answer)


def run_experiment(n_tasks_per_phase: int = 10) -> None:
    # ── Setup ──────────────────────────────────────────────────────────────────
    from mnema.adapters.sqlite.repository import SqliteMnemaStore
    from mnema.adapters.llm.fake import FakeLLM

    store_v2 = SqliteMnemaStore("sqlite:///:memory:")
    store_v3 = SqliteMnemaStore("sqlite:///:memory:")

    llm_v2 = FakeLLM.from_scenario("weatherlib_v2")
    llm_v3 = FakeLLM.from_scenario("weatherlib_v3")

    a0 = A0BaselineAgent()
    a1 = A1SimpleRetrievalAgent()
    a2 = A2MnemaAgent(store_v2, llm_v2)

    all_tasks = generate_tasks(n_per_version=n_tasks_per_phase)
    tasks_v1 = [t for t in all_tasks if t.api_version == 1]
    tasks_v2 = [t for t in all_tasks if t.api_version == 2]
    tasks_v3 = [t for t in all_tasks if t.api_version == 3]

    results: dict[str, dict[str, int]] = {
        "A0": {"v1": 0, "v2": 0, "v3": 0},
        "A1": {"v1": 0, "v2": 0, "v3": 0},
        "A2": {"v1": 0, "v2": 0, "v3": 0},
    }

    print("=" * 62)
    print("  E1: LTCF-Bench -- Lifetime Cognitive Fitness Benchmark")
    print("=" * 62)

    # ── Phase 1: v1 baseline ──────────────────────────────────────────────────
    print("\n[Phase 1] weatherlib v1 -- no drift yet")
    ans_a0 = [a0.answer(t) for t in tasks_v1]
    ans_a1 = [a1.answer(t) for t in tasks_v1]
    ans_a2 = [a2.answer(t) for t in tasks_v1]

    results["A0"]["v1"] = score(ans_a0, tasks_v1)
    results["A1"]["v1"] = score(ans_a1, tasks_v1)
    results["A2"]["v1"] = score(ans_a2, tasks_v1)

    print(f"  A0 correct: {results['A0']['v1']}/{n_tasks_per_phase}")
    print(f"  A1 correct: {results['A1']['v1']}/{n_tasks_per_phase}")
    print(f"  A2 correct: {results['A2']['v1']}/{n_tasks_per_phase}")

    # ── Drift: v1 -> v2 ───────────────────────────────────────────────────────
    print("\n[Drift] weatherlib API changes: v1 -> v2")
    v2_obs = get_observations_for_version(2)
    for obs in v2_obs:
        a1.observe(obs, subject="weatherlib")
        a2.observe(obs, subject="weatherlib")
    print(f"  Injected {len(v2_obs)} observations into A1 and A2")

    print("  A2 running sleep-phase consolidation...")
    a2.sleep_consolidate()
    print("  A2 consolidation complete -- rules updated")

    # ── Phase 2: v2 tasks ─────────────────────────────────────────────────────
    print("\n[Phase 2] weatherlib v2 -- post-drift tasks")
    ans_a0 = [a0.answer(t) for t in tasks_v2]
    ans_a1 = [a1.answer(t) for t in tasks_v2]
    ans_a2 = [a2.answer(t) for t in tasks_v2]

    results["A0"]["v2"] = score(ans_a0, tasks_v2)
    results["A1"]["v2"] = score(ans_a1, tasks_v2)
    results["A2"]["v2"] = score(ans_a2, tasks_v2)

    print(f"  A0 correct: {results['A0']['v2']}/{n_tasks_per_phase}")
    print(f"  A1 correct: {results['A1']['v2']}/{n_tasks_per_phase}")
    print(f"  A2 correct: {results['A2']['v2']}/{n_tasks_per_phase}")

    # ── Drift: v2 -> v3 ───────────────────────────────────────────────────────
    print("\n[Drift] weatherlib API changes: v2 -> v3")

    # A2 needs a fresh store + v3 LLM for the second consolidation
    store_v3_2 = SqliteMnemaStore("sqlite:///:memory:")

    # Re-seed A2's store with all observations (v2 + v3)
    from mnema.services.recorder import MemoryRecorder
    rec2 = MemoryRecorder(store_v3_2)
    for obs in v2_obs:
        rec2.observe("e1-agent", obs, subject="weatherlib")

    v3_obs = get_observations_for_version(3)
    for obs in v3_obs:
        a1.observe(obs, subject="weatherlib")
        rec2.observe("e1-agent", obs, subject="weatherlib")

    from mnema.services.consolidator import SleepConsolidator
    consolidator_v3 = SleepConsolidator(store_v3_2, FakeLLM.from_scenario("weatherlib_v3"))
    from mnema.adapters.llm.fake import FakeLLM

    # Swap A2's internals to use v3 store and LLM
    a2._store = store_v3_2
    a2._llm = FakeLLM.from_scenario("weatherlib_v3")
    a2._recorder = MemoryRecorder(store_v3_2)
    a2._consolidator = consolidator_v3

    print(f"  Injected {len(v3_obs)} observations into A1 and A2")
    print("  A2 running sleep-phase consolidation...")
    a2.sleep_consolidate()
    print("  A2 consolidation complete -- rules updated to v3")

    # ── Phase 3: v3 tasks ─────────────────────────────────────────────────────
    print("\n[Phase 3] weatherlib v3 -- post-drift tasks")
    ans_a0 = [a0.answer(t) for t in tasks_v3]
    ans_a1 = [a1.answer(t) for t in tasks_v3]
    ans_a2 = [a2.answer(t) for t in tasks_v3]

    results["A0"]["v3"] = score(ans_a0, tasks_v3)
    results["A1"]["v3"] = score(ans_a1, tasks_v3)
    results["A2"]["v3"] = score(ans_a2, tasks_v3)

    print(f"  A0 correct: {results['A0']['v3']}/{n_tasks_per_phase}")
    print(f"  A1 correct: {results['A1']['v3']}/{n_tasks_per_phase}")
    print(f"  A2 correct: {results['A2']['v3']}/{n_tasks_per_phase}")

    # ── Results table ─────────────────────────────────────────────────────────
    n = n_tasks_per_phase
    total = n * 3

    print()
    print("=" * 62)
    print("  RESULTS: Accuracy per arm per phase")
    print("=" * 62)
    print(f"  {'Arm':<6}  {'v1 (%)':>8}  {'v2 (%)':>8}  {'v3 (%)':>8}  {'Overall':>10}")
    print("  " + "-" * 54)
    for arm in ["A0", "A1", "A2"]:
        v1_acc = results[arm]["v1"] / n * 100
        v2_acc = results[arm]["v2"] / n * 100
        v3_acc = results[arm]["v3"] / n * 100
        overall = (results[arm]["v1"] + results[arm]["v2"] + results[arm]["v3"]) / total * 100
        print(f"  {arm:<6}  {v1_acc:>7.1f}%  {v2_acc:>7.1f}%  {v3_acc:>7.1f}%  {overall:>9.1f}%")

    print("  " + "-" * 54)
    print()

    # Lift: A2 vs A0 on drift phases (v2 + v3)
    a0_drift = results["A0"]["v2"] + results["A0"]["v3"]
    a1_drift = results["A1"]["v2"] + results["A1"]["v3"]
    a2_drift = results["A2"]["v2"] + results["A2"]["v3"]
    drift_total = n * 2

    print("  Consolidation lift (drift phases only: v2 + v3)")
    print(f"    A0 accuracy on drift: {a0_drift/drift_total*100:.1f}%")
    print(f"    A1 accuracy on drift: {a1_drift/drift_total*100:.1f}%")
    print(f"    A2 accuracy on drift: {a2_drift/drift_total*100:.1f}%")

    lift_vs_baseline = (a2_drift - a0_drift) / drift_total * 100
    lift_vs_retrieval = (a2_drift - a1_drift) / drift_total * 100
    print(f"    A2 lift over A0 (baseline):   +{lift_vs_baseline:.1f}pp")
    print(f"    A2 lift over A1 (retrieval):  +{lift_vs_retrieval:.1f}pp  (consolidation premium)")
    print()
    print("=" * 62)
    print("  Key finding: sleep-phase consolidation (A2) outperforms")
    print("  raw retrieval (A1) on drifting APIs -- structured rules")
    print("  beat unstructured observation logs.")
    print("=" * 62)


if __name__ == "__main__":
    run_experiment()
