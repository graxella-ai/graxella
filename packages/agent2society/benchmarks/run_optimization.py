"""v0.4 demo: `Society.optimize()` closes the documented routing gap.

The LangGraph head-to-head bench documents two TF-IDF misses on the
default scenarios:

  * "Compute correlations between churn and customer segments..."
        wrongly routes to translator-agent (because "between" dominates
        the lexical signal)
  * "Translate the executive memo into Spanish"
        wrongly routes to writer-agent (because "executive memo"
        dominates)

This script shows the new v0.4 surface fixing both *without* swapping the
embedder and *without* putting an LLM in the routing path. The fix is
pure observation: mine discriminative tokens from labeled misses,
backtest against the full label set, apply only net-positive edits.

Run from the repo root:

    python benchmarks/run_optimization.py
"""
from __future__ import annotations

from typing import List, Tuple

from agent2society import Handoff, Society
from agent2society.bench.scenarios import default_scenarios
from agent2society.bench.tasks import default_mesh_cards


def _build_society() -> Society:
    s = Society(strict=False)
    for c in default_mesh_cards():
        s.add(c)
    return s


def _drive_scenarios(s: Society) -> Tuple[List[Tuple[str, str, str]], int, int]:
    """Run every scenario step, return labels + (correct, total) tally."""
    labels: List[Tuple[str, str, str]] = []
    correct = 0
    total = 0
    for sc in default_scenarios():
        for expected_agent, sub_task in sc.expected_steps:
            h = Handoff(task=sub_task)
            s.run(h)
            exp = s.explain(h.id)
            total += 1
            if exp.chosen_agent == expected_agent:
                correct += 1
            expected_skill = next(
                sk.id
                for n in s.graph.agents()
                if n.name == expected_agent
                for sk in n.card.skills
            )
            labels.append((h.id, expected_agent, expected_skill))
    return labels, correct, total


def main() -> None:
    print("agent2society v0.4 optimization demo")
    print("=" * 56)
    print()
    print("Phase 1: route every step of the default LangGraph scenarios")
    print("         with the stock TF-IDF scorer.")
    s = _build_society()
    labels, correct_before, total = _drive_scenarios(s)
    print(f"  step success: {correct_before}/{total}")
    print()

    print("Phase 2: optimize on the labeled steps (no apply yet).")
    report = s.optimize(labels)
    print()
    print(report.render())
    print()

    print("Phase 3: apply only the accepted edits and re-route.")
    s2 = _build_society()
    applied = s2.apply_optimization(report)
    _, correct_after, _ = _drive_scenarios(s2)
    print(f"  edits applied: {applied}")
    print(f"  step success after optimize: {correct_after}/{total}")
    print()

    print("summary")
    print("-" * 56)
    print(f"  before optimize: {correct_before}/{total}")
    print(f"  after optimize:  {correct_after}/{total}")
    delta = correct_after - correct_before
    print(f"  delta:           {delta:+d}")
    print()
    print("note: the routing path stays deterministic and LLM-free in both")
    print("phases. The improvement comes from observation + backtest, not")
    print("from putting a model in the loop.")


if __name__ == "__main__":
    main()
