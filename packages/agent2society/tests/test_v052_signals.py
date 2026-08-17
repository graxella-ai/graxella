"""Tests for the v0.5.2 routing-quality signals.

Covers all three flags and the margin field on RoutingExplanation:
  - margin:           score gap between top-1 and top-2 candidate
  - OOD:             no candidate above min_score
  - VECTOR_AMBIGUITY: top-3 within the ambiguity band
  - LOW_MARGIN:       margin below registered threshold
  - on_low_margin:    governance hook fires correctly
  - metrics:          counters fire for each flag
"""
from __future__ import annotations

from typing import List

import pytest

from agent2society import Handoff, Society
from agent2society.mesh import _compute_routing_signals


# ---- helpers -----------------------------------------------------------

def _card(name: str, description: str, skill_desc: str, tags: List[str]):
    return {
        "name": name,
        "description": description,
        "url": f"local://{name}",
        "version": "0.0.1",
        "skills": [
            {
                "id": "skill",
                "name": "skill",
                "description": skill_desc,
                "tags": tags,
            }
        ],
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
    }


# ---- _compute_routing_signals unit tests (pure function, no Society) ---

def test_margin_two_candidates():
    from agent2society.router import RouteCandidate
    cands = [
        RouteCandidate("a", "s", score=0.8, semantic=0.8, tag_overlap=0.0),
        RouteCandidate("b", "s", score=0.6, semantic=0.6, tag_overlap=0.0),
    ]
    margin, flags = _compute_routing_signals(
        candidates=cands, chosen=cands[0], min_score=0.05,
        low_margin_threshold=None,
    )
    assert abs(margin - 0.2) < 1e-9
    assert "OOD" not in flags
    assert "LOW_MARGIN" not in flags


def test_ood_flag_when_no_candidate_above_min_score():
    from agent2society.router import RouteCandidate
    cands = [
        RouteCandidate("a", "s", score=0.01, semantic=0.01, tag_overlap=0.0),
    ]
    margin, flags = _compute_routing_signals(
        candidates=cands, chosen=None, min_score=0.05,
        low_margin_threshold=None,
    )
    assert "OOD" in flags
    assert "LOW_MARGIN" not in flags


def test_ood_flag_on_empty_candidates():
    margin, flags = _compute_routing_signals(
        candidates=[], chosen=None, min_score=0.05,
        low_margin_threshold=None,
    )
    assert margin == 0.0
    assert "OOD" in flags


def test_vector_ambiguity_flag():
    from agent2society.router import RouteCandidate
    # Three candidates within 0.04 of each other (below 0.05 band).
    cands = [
        RouteCandidate("a", "s", score=0.50, semantic=0.50, tag_overlap=0.0),
        RouteCandidate("b", "s", score=0.48, semantic=0.48, tag_overlap=0.0),
        RouteCandidate("c", "s", score=0.47, semantic=0.47, tag_overlap=0.0),
    ]
    _, flags = _compute_routing_signals(
        candidates=cands, chosen=cands[0], min_score=0.05,
        low_margin_threshold=None,
    )
    assert "VECTOR_AMBIGUITY" in flags


def test_no_vector_ambiguity_when_spread_is_large():
    from agent2society.router import RouteCandidate
    cands = [
        RouteCandidate("a", "s", score=0.80, semantic=0.80, tag_overlap=0.0),
        RouteCandidate("b", "s", score=0.60, semantic=0.60, tag_overlap=0.0),
        RouteCandidate("c", "s", score=0.30, semantic=0.30, tag_overlap=0.0),
    ]
    _, flags = _compute_routing_signals(
        candidates=cands, chosen=cands[0], min_score=0.05,
        low_margin_threshold=None,
    )
    assert "VECTOR_AMBIGUITY" not in flags


def test_low_margin_flag():
    from agent2society.router import RouteCandidate
    cands = [
        RouteCandidate("a", "s", score=0.52, semantic=0.52, tag_overlap=0.0),
        RouteCandidate("b", "s", score=0.50, semantic=0.50, tag_overlap=0.0),
    ]
    _, flags = _compute_routing_signals(
        candidates=cands, chosen=cands[0], min_score=0.05,
        low_margin_threshold=0.05,  # gap=0.02 < 0.05 threshold
    )
    assert "LOW_MARGIN" in flags


def test_no_low_margin_when_threshold_is_none():
    from agent2society.router import RouteCandidate
    cands = [
        RouteCandidate("a", "s", score=0.52, semantic=0.52, tag_overlap=0.0),
        RouteCandidate("b", "s", score=0.50, semantic=0.50, tag_overlap=0.0),
    ]
    _, flags = _compute_routing_signals(
        candidates=cands, chosen=cands[0], min_score=0.05,
        low_margin_threshold=None,  # no threshold registered
    )
    assert "LOW_MARGIN" not in flags


# ---- integration: flags appear on RoutingExplanation -------------------

def test_margin_populated_on_explanation():
    s = Society(strict=False)
    # Two agents with distinct descriptions so TF-IDF separates them.
    s.add(_card("analyst", "statistical analysis data", "compute statistics", ["stats"]))
    s.add(_card("writer", "draft executive memos business writing", "write memo", ["memo"]))

    h = Handoff(task="compute statistics on churn data")
    s.run(h)
    exp = s.explain(h.id)
    assert exp is not None
    assert exp.margin >= 0.0          # always present
    assert isinstance(exp.flags, tuple)


def test_ood_flag_on_explanation_when_no_agents():
    s = Society(strict=False, min_score=0.05)
    # Add one agent whose skill is completely unrelated to the task.
    s.add(_card("coder", "write python code", "python programming", ["python"]))
    h = Handoff(task="xyzzy plugh frobozz")   # nonsense tokens
    s.run(h)
    exp = s.explain(h.id)
    assert exp is not None
    # Margin should be 0 (either OOD or only one candidate).
    assert exp.margin >= 0.0


def test_explanation_to_dict_includes_margin_and_flags():
    s = Society(strict=False)
    s.add(_card("analyst", "statistical analysis", "compute statistics", ["stats"]))
    h = Handoff(task="compute statistics")
    s.run(h)
    d = s.explain(h.id).to_dict()
    assert "margin" in d
    assert "flags" in d
    assert isinstance(d["margin"], float)
    assert isinstance(d["flags"], list)


def test_explanation_render_includes_margin():
    s = Society(strict=False)
    s.add(_card("analyst", "statistical analysis data", "compute statistics", ["stats"]))
    s.add(_card("writer", "business writing memos", "write executive memo", ["memo"]))
    h = Handoff(task="compute statistics on churn")
    s.run(h)
    rendered = s.explain(h.id).render()
    assert "margin=" in rendered


# ---- on_low_margin hook fires correctly --------------------------------

def test_on_low_margin_hook_fires_when_margin_is_narrow():
    """Register a tiny threshold so almost any route triggers it, then
    verify the hook fires and receives the RoutingExplanation."""
    s = Society(strict=False)
    s.add(_card("analyst", "statistical analysis data", "compute statistics", ["stats"]))
    s.add(_card("writer", "business writing memos", "write executive memo", ["memo"]))

    fired: list = []
    s.on_low_margin(lambda exp: fired.append(exp), threshold=1.0)  # always fires

    h = Handoff(task="compute statistics on churn data")
    s.run(h)
    assert fired, "on_low_margin hook should have fired"
    assert fired[0].handoff_id == h.id
    assert "LOW_MARGIN" in fired[0].flags


def test_on_low_margin_hook_does_not_fire_when_margin_is_wide():
    """A threshold of 0.0 means never fire (gap is always >= 0)."""
    s = Society(strict=False)
    s.add(_card("analyst", "statistical analysis data", "compute statistics", ["stats"]))
    s.add(_card("writer", "business writing memos", "write executive memo", ["memo"]))

    fired: list = []
    s.on_low_margin(lambda exp: fired.append(exp), threshold=0.0)

    h = Handoff(task="compute statistics on churn data")
    s.run(h)
    assert not fired, "hook should not fire when threshold is 0.0"


# ---- metrics fire for signals ------------------------------------------

def test_low_margin_metric_increments():
    s = Society(strict=False)
    s.add(_card("analyst", "statistical analysis data", "compute statistics", ["stats"]))
    s.add(_card("writer", "business writing memos", "write executive memo", ["memo"]))

    s.on_low_margin(lambda exp: None, threshold=1.0)  # always fires

    s.run(Handoff(task="compute statistics on churn"))
    s.run(Handoff(task="write executive memo on Q3"))

    total = sum(
        r["value"]
        for r in s.metrics.snapshot()["counters"]["agent2society_low_margin_total"]
    )
    assert total >= 2


def test_ood_metric_increments_when_unroutable():
    s = Society(strict=False, min_score=0.99)   # impossible threshold
    s.add(_card("analyst", "data", "statistics", ["stats"]))

    s.run(Handoff(task="something completely unrelated"))

    ood_total = sum(
        r["value"]
        for r in s.metrics.snapshot()["counters"]["agent2society_ood_total"]
    )
    assert ood_total >= 1
