"""Baseline: bind ALL 10 skills to the LLM for every query.

This is the Anthropic-style default: put every capability in-context,
let the model reason about which to call. Works great when the model is
Sonnet or Opus. On qwen2.5:3b the story is different — the prompt bloats
with irrelevant schemas AND the small model's attention thins across them,
so selection accuracy drops.

Prints per-task metrics and a totals line. Meant to be imported by
run_compare.py, but can be run standalone.
"""
from __future__ import annotations

from progressive_skills.harness import (RunResult, check_ollama,
                                        invoke_with_tools)
from progressive_skills.skills import SKILLS
from progressive_skills.tasks import TASKS


def run_flat() -> list[RunResult]:
    """Every task sees every skill. No orchestration."""
    results: list[RunResult] = []
    for task in TASKS:
        r = invoke_with_tools(task.query, SKILLS, task.expected_skills)
        results.append(r)
    return results


if __name__ == "__main__":
    ok = check_ollama()
    print(f"Ollama reachable: {ok}\n")
    results = run_flat()
    print(f"{'query':<70} {'exp':>3} {'called':>6} {'tok_in':>7} {'ok':>3}")
    print("-" * 96)
    for r in results:
        marker = "?" if r.success is None else ("Y" if r.success else "N")
        print(f"{r.query[:70]:<70} {len(r.expected):>3} "
              f"{len(r.called):>6} {r.tokens_prompt:>7} {marker:>3}")
    ok_n = sum(1 for r in results if r.success)
    total_tok = sum(r.tokens_prompt + r.tokens_completion for r in results)
    print("-" * 96)
    print(f"success: {ok_n}/{len(results)}   total tokens: {total_tok}")
