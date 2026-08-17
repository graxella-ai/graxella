"""Governance detectors and hooks.

This module is detection-only. It does not auto-correct, retry, or
re-route -- it surfaces the signal so a human (or a custom handler the
developer wires up) can decide what to do. That separation is
deliberate: silent auto-correction is exactly the opacity layer that
makes multi-agent systems untrustworthy.

Two detectors ship in v1.5:

* `ConflictDetector` -- flags when "the same task text was routed to
  different (agent, skill) pairs across handoffs in the window." That
  is a real-world signal: either the index is non-deterministic
  (capability drift), or the task text is genuinely ambiguous and a
  human should disambiguate.

* `LowConfidenceDetector` -- flags handoffs whose chosen-score fell
  below the per-handoff `confidence_required` threshold.

Hooks (registered on Mesh) fire as side effects of `mesh.run()`. They
do not mutate the dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from .explanation import RoutingExplanation


# ---- hook signatures -----------------------------------------------------

ConflictHook = Callable[["Conflict"], None]
LowConfidenceHook = Callable[[RoutingExplanation], None]
LowMarginHook = Callable[[RoutingExplanation], None]
CapabilityDriftHook = Callable[["CapabilityDrift"], None]
HumanReviewHook = Callable[[RoutingExplanation, str], None]


# ---- detector outputs ----------------------------------------------------

@dataclass(frozen=True)
class Conflict:
    """Two handoffs that produced a coordination conflict."""

    kind: str
    detail: str
    handoff_ids: List[str] = field(default_factory=list)
    agents: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "handoff_ids": list(self.handoff_ids),
            "agents": list(self.agents),
        }


@dataclass(frozen=True)
class CapabilityDrift:
    """A single agent picked for different skill ids on similar tasks."""

    agent: str
    skills_seen: List[str]
    sample_handoff_ids: List[str]
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "skills_seen": list(self.skills_seen),
            "sample_handoff_ids": list(self.sample_handoff_ids),
            "detail": self.detail,
        }


# ---- detectors -----------------------------------------------------------


def _normalise(task: str) -> str:
    """Cheap task-text normalisation for equality grouping."""
    return " ".join(task.lower().split())


class ConflictDetector:
    """Detects routing conflicts across a window of recent handoffs.

    The default check is "same task text, different chosen (agent, skill)
    across handoffs". That covers the practical case where a developer
    sees their fleet route the same prompt to different agents on
    different runs -- a real, observable failure of legibility.

    A subclass can override `detect()` for richer comparisons. Output
    semantic conflict detection is left to v2 because it requires
    output-aware comparators that need access to the actual produced
    text from prior runs.
    """

    def __init__(self, *, window: int = 50) -> None:
        self.window = max(1, int(window))

    def detect(
        self, explanations: Sequence[RoutingExplanation]
    ) -> List[Conflict]:
        if not explanations:
            return []
        recent = list(explanations)[-self.window :]
        by_task: Dict[str, List[RoutingExplanation]] = {}
        for e in recent:
            if e.chosen_agent is None:
                continue
            key = _normalise(e.task)
            by_task.setdefault(key, []).append(e)

        conflicts: List[Conflict] = []
        for key, group in by_task.items():
            distinct = {(e.chosen_agent, e.chosen_skill) for e in group}
            if len(distinct) > 1:
                ids = [e.handoff_id for e in group]
                agents = sorted({e.chosen_agent or "" for e in group})
                conflicts.append(
                    Conflict(
                        kind="same_task_different_agent",
                        detail=(
                            f"task {key[:60]!r} was routed to "
                            f"{len(distinct)} different (agent, skill) "
                            f"pairs across {len(group)} handoffs"
                        ),
                        handoff_ids=ids,
                        agents=agents,
                    )
                )
        return conflicts


class CapabilityDriftDetector:
    """Detects when one agent gets picked for multiple skills across runs.

    Not inherently bad -- agents legitimately have multiple skills. The
    signal is useful when an agent is the routing default for many
    different task shapes; that often means its skill descriptions are
    too broad and the index is under-discriminating.
    """

    def __init__(self, *, min_distinct_skills: int = 3) -> None:
        self.min_distinct_skills = max(2, int(min_distinct_skills))

    def detect(
        self, explanations: Sequence[RoutingExplanation]
    ) -> List[CapabilityDrift]:
        per_agent: Dict[str, Dict[str, List[str]]] = {}
        for e in explanations:
            if not e.chosen_agent or not e.chosen_skill:
                continue
            per_agent.setdefault(e.chosen_agent, {}).setdefault(
                e.chosen_skill, []
            ).append(e.handoff_id)

        drifts: List[CapabilityDrift] = []
        for agent, skills in per_agent.items():
            if len(skills) >= self.min_distinct_skills:
                samples: List[str] = []
                for ids in skills.values():
                    samples.append(ids[0])
                drifts.append(
                    CapabilityDrift(
                        agent=agent,
                        skills_seen=sorted(skills.keys()),
                        sample_handoff_ids=samples,
                        detail=(
                            f"agent {agent!r} was selected across "
                            f"{len(skills)} distinct skills"
                        ),
                    )
                )
        return drifts


# ---- hook registry -------------------------------------------------------


@dataclass
class GovernanceHooks:
    """Holds the callbacks the Mesh fires during run().

    Each hook list is empty by default. Adding a hook is purely additive
    -- there is no way for a hook to block or modify a dispatch. The
    Mesh owns the actual firing logic so it can attach failures to
    telemetry and de-duplicate against earlier fires.
    """

    on_conflict: List[ConflictHook] = field(default_factory=list)
    on_low_confidence: List[LowConfidenceHook] = field(default_factory=list)
    on_low_margin: List[LowMarginHook] = field(default_factory=list)
    on_capability_drift: List[CapabilityDriftHook] = field(default_factory=list)
    on_human_review: List[HumanReviewHook] = field(default_factory=list)
    low_confidence_threshold: Optional[float] = None
    low_margin_threshold: Optional[float] = None
