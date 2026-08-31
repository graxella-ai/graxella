"""Tiered recall: keep breadth, spend the token budget on depth only where
it matters (the L0/L2 idea applied to Mnema recall). Pure + deterministic —
no model needed. Verifies the honest contract:

  * fits the char budget,
  * the single most-relevant case is shown in FULL (not truncated),
  * at the same budget, tiered surfaces >= as many distinct precedents as the
    flat renderer (strictly more once tasks are realistically long),
  * empty in -> empty out.
"""
from __future__ import annotations

from graxella.beliefs.records import (RecalledCase, render_recall_block,
                                      render_recall_tiered)

_LONG = ("customer ticket {i}: refund requested for a damaged item that "
         "arrived broken and late, the buyer is now asking for escalation")


def _cases(n: int) -> list[RecalledCase]:
    return [RecalledCase(similarity=0.9 - 0.05 * i, task=_LONG.format(i=i),
                         chosen=f"agent_{i}", ok=(i % 3 != 0), completion=0.9,
                         err=None if i % 3 else "HTTP_410_GONE schema deprecated")
            for i in range(n)]


def _precedents(block: str, n: int) -> int:
    return sum(1 for i in range(n) if f"agent_{i}" in block)


def test_empty_in_empty_out():
    assert render_recall_tiered([]) == ""


def test_fits_budget_and_top_case_is_full():
    cases = _cases(8)
    block = render_recall_tiered(cases, max_chars=800, detail_k=1)
    assert len(block) <= 800
    # the most-relevant case's FULL task (its tail) survives — flat truncates
    assert "asking for escalation" in block.split("\n")[1]


def test_breadth_beats_flat_at_same_budget():
    cases, budget = _cases(8), 800
    flat = _precedents(render_recall_block(cases, max_chars=budget), 8)
    tiered = _precedents(render_recall_tiered(cases, max_chars=budget,
                                              detail_k=1), 8)
    assert tiered >= flat
    assert tiered >= 5           # headlines are cheap: breadth is preserved


def test_detail_k_controls_depth():
    cases = _cases(6)
    b1 = render_recall_tiered(cases, max_chars=1200, detail_k=1)
    b3 = render_recall_tiered(cases, max_chars=1200, detail_k=3)
    # more detail cases -> more full "outcome:" lines rendered
    assert b3.count("outcome:") > b1.count("outcome:")


def test_degrades_to_single_headline_under_tiny_budget():
    block = render_recall_tiered(_cases(4), max_chars=90, detail_k=1)
    assert block and len(block.split("\n")) <= 3   # header + 1 line + footer
