"""v1.5 governance: ConflictDetector, CapabilityDriftDetector, hooks.

All governance in agent2society is detection-only. Hooks are side effects --
they cannot block, retry, or modify a dispatch. These tests verify the
contract and that user-supplied handlers cannot break the dispatch loop
even if they raise.
"""
from __future__ import annotations

from typing import List

import pytest

import agent2society
from agent2society import (
    CapabilityDrift,
    CapabilityDriftDetector,
    Conflict,
    ConflictDetector,
    Handoff,
    Mesh,
    RoutingExplanation,
)


# --- fixtures: two-agent mesh wired to a fake transport -------------------


class _FakeWriter:
    name = "writer-agent"
    description = "drafts memos"
    skills = [
        {
            "id": "exec_memo",
            "name": "Executive Memo",
            "description": "Drafts executive memos from notes.",
            "tags": ["writing", "memo", "exec"],
        },
        {
            "id": "blog_post",
            "name": "Blog Post",
            "description": "Drafts a blog post from notes.",
            "tags": ["writing", "blog", "post"],
        },
        {
            "id": "press_release",
            "name": "Press Release",
            "description": "Drafts a press release for launches.",
            "tags": ["writing", "release", "press", "launch"],
        },
    ]

    def run(self, task):
        return f"WROTE: {task}"


class _FakeResearcher:
    name = "research-agent"
    description = "searches and summarises"
    skills = [
        {
            "id": "web_research",
            "name": "Web Research",
            "description": "Web research and summary.",
            "tags": ["research", "web", "search"],
        }
    ]

    def __call__(self, task):
        return f"FOUND: {task}"


def _mesh_with_both() -> Mesh:
    m = Mesh()
    m.add(_FakeResearcher())
    m.add(_FakeWriter())
    return m


# --- low-confidence hook --------------------------------------------------


def test_on_low_confidence_fires_when_handoff_threshold_not_met():
    m = _mesh_with_both()
    fired: List[RoutingExplanation] = []
    m.on_low_confidence(fired.append)

    # The TF-IDF score on a tiny mesh is well below 0.95 even for a
    # well-matched task. The handoff sets an aggressive threshold to
    # force the hook to fire.
    h = Handoff(task="Draft an exec memo", confidence_required=0.95)
    m.run(h)

    assert len(fired) == 1
    assert fired[0].handoff_id == h.id
    assert fired[0].confidence < 0.95


def test_on_low_confidence_threshold_arg_applies_when_handoff_unset():
    m = _mesh_with_both()
    fired: List[RoutingExplanation] = []
    m.on_low_confidence(fired.append, threshold=0.95)

    # No per-handoff threshold; the mesh-wide one kicks in instead.
    m.run("Draft an exec memo")
    assert len(fired) == 1


def test_low_confidence_handler_exception_does_not_break_run():
    m = _mesh_with_both()

    def broken(_exp):
        raise RuntimeError("boom")

    m.on_low_confidence(broken, threshold=0.99)
    # If exceptions leaked, this would raise.
    out = m.run("Draft a memo")
    assert "WROTE" in out


# --- human-review hook ----------------------------------------------------


def test_on_human_review_fires_when_predicate_returns_true():
    m = _mesh_with_both()
    seen: List[str] = []
    m.on_human_review(lambda _exp, result: seen.append(result))

    h = Handoff(
        task="Draft a memo about a regulated topic",
        human_review_when=lambda result: "regulated" in result,
    )
    m.run(h)
    # FakeWriter echoes the task verbatim, so "regulated" appears.
    assert seen == ["WROTE: Draft a memo about a regulated topic"]


def test_on_human_review_predicate_errors_are_swallowed():
    m = _mesh_with_both()
    m.on_human_review(lambda _exp, result: None)

    def angry_predicate(result):
        raise ValueError("nope")

    h = Handoff(task="Draft a memo", human_review_when=angry_predicate)
    # The predicate raises; the run should still complete normally.
    out = m.run(h)
    assert "WROTE" in out


# --- conflict detection ---------------------------------------------------


def test_conflict_detector_flags_same_task_routed_to_different_agents():
    e1 = RoutingExplanation(
        handoff_id="h1",
        task="Summarise the docs",
        intent="",
        chosen_agent="research-agent",
        chosen_skill="web_research",
        rationale="r",
        features_fired={},
        alternatives=[],
        confidence=0.4,
    )
    e2 = RoutingExplanation(
        handoff_id="h2",
        task="Summarise the docs",
        intent="",
        chosen_agent="writer-agent",
        chosen_skill="exec_memo",
        rationale="r",
        features_fired={},
        alternatives=[],
        confidence=0.4,
    )
    d = ConflictDetector(window=10)
    conflicts = d.detect([e1, e2])
    assert len(conflicts) == 1
    assert conflicts[0].kind == "same_task_different_agent"
    assert set(conflicts[0].handoff_ids) == {"h1", "h2"}


def test_conflict_detector_does_not_flag_identical_routes():
    e1 = RoutingExplanation(
        handoff_id="h1",
        task="Summarise the docs",
        intent="",
        chosen_agent="writer-agent",
        chosen_skill="exec_memo",
        rationale="r",
        features_fired={},
        alternatives=[],
        confidence=0.4,
    )
    e2 = RoutingExplanation(
        handoff_id="h2",
        task="Summarise the docs",
        intent="",
        chosen_agent="writer-agent",
        chosen_skill="exec_memo",
        rationale="r",
        features_fired={},
        alternatives=[],
        confidence=0.4,
    )
    assert ConflictDetector(window=10).detect([e1, e2]) == []


# --- capability drift -----------------------------------------------------


def test_capability_drift_detector_flags_agent_with_many_skills():
    exps = [
        RoutingExplanation(
            handoff_id=f"h{i}",
            task=f"task {i}",
            intent="",
            chosen_agent="writer-agent",
            chosen_skill=skill,
            rationale="r",
            features_fired={},
            alternatives=[],
            confidence=0.4,
        )
        for i, skill in enumerate(["exec_memo", "blog_post", "press_release"])
    ]
    drifts = CapabilityDriftDetector(min_distinct_skills=3).detect(exps)
    assert len(drifts) == 1
    drift = drifts[0]
    assert drift.agent == "writer-agent"
    assert set(drift.skills_seen) == {"exec_memo", "blog_post", "press_release"}


def test_drift_detector_does_not_flag_below_threshold():
    exps = [
        RoutingExplanation(
            handoff_id="h1",
            task="t",
            intent="",
            chosen_agent="writer-agent",
            chosen_skill="exec_memo",
            rationale="r",
            features_fired={},
            alternatives=[],
            confidence=0.4,
        ),
        RoutingExplanation(
            handoff_id="h2",
            task="t",
            intent="",
            chosen_agent="writer-agent",
            chosen_skill="blog_post",
            rationale="r",
            features_fired={},
            alternatives=[],
            confidence=0.4,
        ),
    ]
    # Default min_distinct_skills is 3; only 2 here.
    assert CapabilityDriftDetector().detect(exps) == []


# --- end-to-end: hooks fire during mesh.run() -----------------------------


def test_mesh_fires_on_conflict_hook_across_two_runs():
    """Same task routed differently across two runs => one conflict hook fire."""
    # Build two slightly different mesh configurations so the same task
    # gets different routing decisions deterministically.
    m1 = _mesh_with_both()
    seen: List[Conflict] = []
    m1.on_conflict(seen.append)

    # First run: write task -> writer-agent.
    m1.run("Draft an exec memo about Q3")
    # Second run with the SAME task: also writer-agent. No conflict.
    m1.run("Draft an exec memo about Q3")
    assert seen == []

    # Now manually splice a different chosen_agent into a second
    # explanation to simulate the cross-run drift the detector targets.
    # This proves the wiring fires the hook when a real conflict appears.
    e1 = m1.last_explanation()
    spoof = RoutingExplanation(
        handoff_id="spoof",
        task=e1.task,
        intent="",
        chosen_agent="research-agent",
        chosen_skill="web_research",
        rationale="r",
        features_fired={},
        alternatives=[],
        confidence=0.4,
    )
    m1._store_explanation("spoof", spoof)
    m1._maybe_fire_governance()
    assert any(c.kind == "same_task_different_agent" for c in seen)


def test_mesh_does_not_fire_same_conflict_twice():
    m = _mesh_with_both()
    seen: List[Conflict] = []
    m.on_conflict(seen.append)

    e1 = RoutingExplanation(
        handoff_id="h1",
        task="t",
        intent="",
        chosen_agent="writer-agent",
        chosen_skill="exec_memo",
        rationale="r",
        features_fired={},
        alternatives=[],
        confidence=0.4,
    )
    e2 = RoutingExplanation(
        handoff_id="h2",
        task="t",
        intent="",
        chosen_agent="research-agent",
        chosen_skill="web_research",
        rationale="r",
        features_fired={},
        alternatives=[],
        confidence=0.4,
    )
    m._store_explanation("h1", e1)
    m._store_explanation("h2", e2)
    m._maybe_fire_governance()
    m._maybe_fire_governance()
    # Same set of handoffs in the same kind: fires once.
    assert len(seen) == 1
