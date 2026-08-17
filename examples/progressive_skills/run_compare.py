"""Side-by-side comparison: flat binding vs. Graxella progressive disclosure.

Runs the same 5 tasks through both approaches and prints:

  * per-task tokens sent to the LLM
  * per-task selection accuracy (right subset of skills called?)
  * total wall time
  * projected $ / 1k tasks at frontier-model pricing

The projection is what closes the loop: if the same open-source model
under Graxella orchestration matches accuracy at 5-10x fewer tokens, then
at frontier prices you would pay 5-10x less to serve the same workload —
which is the 'intelligence per dollar' number a CFO cares about.
"""
from __future__ import annotations

from pathlib import Path

from progressive_skills.harness import (check_ollama, cost_usd,
                                        FRONTIER_INPUT_PER_M,
                                        FRONTIER_OUTPUT_PER_M,
                                        OSS_INPUT_PER_M, OSS_OUTPUT_PER_M)
from progressive_skills.run_flat import run_flat
from progressive_skills.run_graxella import run_progressive


def _print_table(label: str, results: list) -> tuple[int, int, int]:
    print(f"\n=== {label} ===")
    print(f"{'#':>2} {'query':<58} {'exp/called':>10} {'tok_in':>7} "
          f"{'tok_out':>7} {'wall_s':>6} {'ok':>3}")
    print("-" * 96)
    for i, r in enumerate(results, 1):
        marker = "?" if r.success is None else ("Y" if r.success else "N")
        ec = f"{len(r.expected)}/{len(r.called)}"
        print(f"{i:>2} {r.query[:58]:<58} {ec:>10} {r.tokens_prompt:>7} "
              f"{r.tokens_completion:>7} {r.wall_s:>6} {marker:>3}")
    total_in = sum(r.tokens_prompt for r in results)
    total_out = sum(r.tokens_completion for r in results)
    total_wall = round(sum(r.wall_s for r in results), 2)
    ok_n = sum(1 for r in results if r.success)
    unknown_n = sum(1 for r in results if r.success is None)
    print("-" * 96)
    if unknown_n:
        print(f"success: {ok_n}/{len(results)} (LLM skipped for {unknown_n})   "
              f"tokens: {total_in} in / {total_out} out   "
              f"wall: {total_wall}s")
    else:
        print(f"success: {ok_n}/{len(results)}   "
              f"tokens: {total_in} in / {total_out} out   "
              f"wall: {total_wall}s")
    return total_in, total_out, ok_n


def _print_cost_projection(label: str, tin: int, tout: int, n_tasks: int) -> None:
    per_task_in = tin / max(n_tasks, 1)
    per_task_out = tout / max(n_tasks, 1)
    frontier_1k = 1000 * cost_usd(int(per_task_in), int(per_task_out), frontier=True)
    oss_1k = 1000 * cost_usd(int(per_task_in), int(per_task_out), frontier=False)
    print(f"  {label:<20}  per-task in/out: {per_task_in:.0f} / {per_task_out:.0f} tok   "
          f"1k-task cost: ${frontier_1k:.2f} (frontier) / ${oss_1k:.4f} (oss)")


def main() -> None:
    print("=" * 96)
    print("Progressive Skill Disclosure — flat vs. Graxella orchestration")
    print("=" * 96)
    print(f"Ollama: {'reachable' if check_ollama() else 'UNREACHABLE (tokens only)'}")
    print(f"Pricing: frontier ${FRONTIER_INPUT_PER_M}/M in, ${FRONTIER_OUTPUT_PER_M}/M out")
    print(f"         oss      ${OSS_INPUT_PER_M}/M in, ${OSS_OUTPUT_PER_M}/M out")

    flat = run_flat()
    tin_f, tout_f, ok_f = _print_table("FLAT (10 skills bound every call)", flat)

    here = Path(__file__).parent
    cold, warm = run_progressive(here / "_prog_store.db")
    tin_c, tout_c, ok_c = _print_table("GRAXELLA cold (router picks top-5)", cold)
    tin_w, tout_w, ok_w = _print_table("GRAXELLA warm (curated rulebook, K=1..2)", warm)

    n = len(flat)
    print("\n" + "=" * 96)
    print("Cost projection (per task averaged over the batch):")
    print("=" * 96)
    _print_cost_projection("flat", tin_f, tout_f, n)
    _print_cost_projection("graxella cold", tin_c, tout_c, n)
    _print_cost_projection("graxella warm", tin_w, tout_w, n)

    # Bottom-line ratio
    if tin_f > 0:
        ratio_cold = tin_f / max(tin_c, 1)
        ratio_warm = tin_f / max(tin_w, 1)
        print(f"\nInput-token reduction: cold {ratio_cold:.1f}x, warm {ratio_warm:.1f}x")
        if ok_c is not None and ok_f is not None:
            print(f"Selection accuracy   : flat {ok_f}/{n}, "
                  f"graxella cold {ok_c}/{n}, warm {ok_w}/{n}")


if __name__ == "__main__":
    main()
