"""graxella.gate.promoter — PromotionGate + multi-objective scoring.

.. deprecated:: Promotion Spec v0.1 — INTERIM IMPLEMENTATION.
    The scored ``GatePolicy`` (hand-tuned weights) is the explicitly
    rejected gate design. The canonical schema is
    ``graxella.gate.spec.Proposal`` (docs/specs/promotion-spec.md) and the
    replacement is the mnema-grounded Evidence Gate (Phase 1, build plan
    tasks 1-1…1-5), after which this module is deleted. Do not build new
    features against ``GatePolicy``/``ObjectiveScores``.

Contract: every learned strategy is a Proposal. A proposal only becomes
active policy when its status transitions to ACTIVE via ``approve()``
followed by ``activate()`` -- which requires either (a) an automated
policy that clears the gate's threshold, or (b) explicit human sign-off.
Nothing else promotes.

Beat 1.5 adds ``ObjectiveScores`` + ``GatePolicy`` so proposals can be
graded on a vector (cost / latency / quality / compliance) with a
compliance hard-floor and a blast-radius override that forces "wide"
proposals to human review regardless of score.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Callable


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"   # policy passed but not yet active
    ACTIVE = "active"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class GateDecision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    NEEDS_HUMAN = "needs_human"
    AUTO_REJECT = "auto_reject"


@dataclass(frozen=True)
class ObjectiveScores:
    """A proposal's multi-objective grade.

    * ``cost_usd`` / ``latency_ms``: lower is better (0..reference band).
    * ``quality`` / ``compliance``: higher is better (0..1).
    """

    cost_usd: float = 0.0
    latency_ms: float = 0.0
    quality: float = 1.0
    compliance: float = 1.0

    def to_dict(self) -> dict:
        return {
            "cost_usd": self.cost_usd, "latency_ms": self.latency_ms,
            "quality": self.quality, "compliance": self.compliance,
        }


@dataclass
class GatePolicy:
    """Turns an ObjectiveScores vector + blast_radius into a GateDecision.

    Scalar rollup: weighted sum of normalized objectives, all mapped to
    "higher is better" on [0,1]. Cost/latency are inverted against a
    reference band. Compliance below ``compliance_floor`` is a hard
    reject. Blast radius "wide" forces human review regardless of score.
    """

    weights: dict = field(default_factory=lambda: {
        "quality": 0.4, "compliance": 0.3, "cost": 0.2, "latency": 0.1,
    })
    cost_reference: float = 1.0        # USD; cost >= this contributes 0
    latency_reference: float = 1000.0  # ms; latency >= this contributes 0
    compliance_floor: float = 0.9      # below => AUTO_REJECT
    auto_approve: float = 0.85         # scalar >= this => AUTO_APPROVE
    needs_human_min: float = 0.5       # scalar in [min, auto_approve) => NEEDS_HUMAN
    wide_blast_needs_human: bool = True

    def scalar(self, o: ObjectiveScores) -> float:
        """Compute the [0,1] weighted rollup. Weights need not sum to 1
        (they're re-normalized); missing weights default to 0."""
        w = self.weights
        wq, wcm = float(w.get("quality", 0.0)), float(w.get("compliance", 0.0))
        wc, wl = float(w.get("cost", 0.0)), float(w.get("latency", 0.0))
        total = wq + wcm + wc + wl
        if total <= 0:
            return 0.0
        cost_norm = 1.0 - min(1.0, max(0.0, o.cost_usd / self.cost_reference))
        latency_norm = 1.0 - min(1.0, max(0.0, o.latency_ms / self.latency_reference))
        raw = (wq * o.quality + wcm * o.compliance
               + wc * cost_norm + wl * latency_norm)
        return raw / total

    def evaluate(self, o: ObjectiveScores, *, blast_radius: str = "unknown") -> GateDecision:
        if o.compliance < self.compliance_floor:
            return GateDecision.AUTO_REJECT
        s = self.scalar(o)
        if self.wide_blast_needs_human and blast_radius == "wide":
            return GateDecision.NEEDS_HUMAN if s >= self.needs_human_min else GateDecision.AUTO_REJECT
        if s >= self.auto_approve:
            return GateDecision.AUTO_APPROVE
        if s >= self.needs_human_min:
            return GateDecision.NEEDS_HUMAN
        return GateDecision.AUTO_REJECT


@dataclass
class Proposal:
    id: int
    kind: str                       # e.g. "route.tag_add", "rule.new", "skill.new"
    payload: dict
    score: float = 0.0              # legacy scalar; equals policy.scalar() if objectives set
    blast_radius: str = "unknown"   # "narrow" | "wide" | "unknown"
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    decided_by: str | None = None
    note: str | None = None
    objectives: ObjectiveScores | None = None


class PromotionGate:
    """In-memory promotion gate with optional multi-objective scoring."""

    def __init__(self, *, threshold: float = 0.85,
                 require_human: bool = True,
                 policy: GatePolicy | None = None) -> None:
        # Legacy args are folded into a policy when one isn't supplied,
        # so existing callers keep working with no behavior change.
        self.threshold = threshold
        self.require_human = require_human
        self.policy = policy or GatePolicy(auto_approve=threshold)
        self._queue: dict[int, Proposal] = {}
        self._seq = itertools.count(1)
        self._lock = Lock()
        self._on_change: list[Callable[[Proposal], None]] = []

    # -- API -----------------------------------------------------------------

    def propose(self, kind: str, payload: dict, *,
                score: float = 0.0,
                blast_radius: str = "unknown",
                objectives: ObjectiveScores | None = None) -> Proposal:
        with self._lock:
            effective_score = self.policy.scalar(objectives) if objectives else float(score)
            p = Proposal(
                id=next(self._seq),
                kind=kind,
                payload=dict(payload),
                score=effective_score,
                blast_radius=blast_radius,
                objectives=objectives,
            )
            self._queue[p.id] = p
        self._fire(p)
        return p

    def approve(self, proposal_id: int, *, by: str, note: str = "") -> Proposal:
        with self._lock:
            p = self._queue[proposal_id]
            p.status = ProposalStatus.APPROVED
            p.decided_at = time.time()
            p.decided_by = by
            p.note = note
        self._fire(p)
        return p

    def activate(self, proposal_id: int, *, by: str) -> Proposal:
        with self._lock:
            p = self._queue[proposal_id]
            if p.status is not ProposalStatus.APPROVED:
                raise ValueError(f"proposal {proposal_id} not approved yet")
            p.status = ProposalStatus.ACTIVE
            p.decided_at = time.time()
            p.decided_by = by
        self._fire(p)
        return p

    def reject(self, proposal_id: int, *, by: str, note: str = "") -> Proposal:
        with self._lock:
            p = self._queue[proposal_id]
            p.status = ProposalStatus.REJECTED
            p.decided_at = time.time()
            p.decided_by = by
            p.note = note
        self._fire(p)
        return p

    def rollback(self, proposal_id: int, *, by: str, note: str = "") -> Proposal:
        with self._lock:
            p = self._queue[proposal_id]
            p.status = ProposalStatus.ROLLED_BACK
            p.decided_at = time.time()
            p.decided_by = by
            p.note = note
        self._fire(p)
        return p

    # -- policy-driven evaluation -------------------------------------------

    def evaluate(self, proposal_id: int) -> GateDecision:
        """Compute the policy decision for a proposal *without* transitioning
        it. Returns AUTO_APPROVE / NEEDS_HUMAN / AUTO_REJECT."""
        with self._lock:
            p = self._queue[proposal_id]
            objectives = p.objectives or ObjectiveScores(quality=p.score, compliance=1.0)
            blast = p.blast_radius
        return self.policy.evaluate(objectives, blast_radius=blast)

    def auto_evaluate(self, proposal_id: int, *,
                      by: str = "policy",
                      note: str = "") -> tuple[GateDecision, Proposal]:
        """Run ``evaluate`` and *also* transition the proposal per the
        decision. NEEDS_HUMAN leaves it PENDING. If ``require_human`` is
        True, AUTO_APPROVE is downgraded to NEEDS_HUMAN (belt-and-braces
        for high-blast-radius environments)."""
        decision = self.evaluate(proposal_id)
        if decision is GateDecision.AUTO_APPROVE and self.require_human:
            decision = GateDecision.NEEDS_HUMAN
        if decision is GateDecision.AUTO_APPROVE:
            p = self.approve(proposal_id, by=by, note=note or "policy auto-approve")
        elif decision is GateDecision.AUTO_REJECT:
            p = self.reject(proposal_id, by=by, note=note or "policy auto-reject")
        else:
            with self._lock:
                p = self._queue[proposal_id]
        return decision, p

    # -- queries -------------------------------------------------------------

    def pending(self) -> list[Proposal]:
        with self._lock:
            return [p for p in self._queue.values()
                    if p.status is ProposalStatus.PENDING]

    def active(self) -> list[Proposal]:
        with self._lock:
            return [p for p in self._queue.values()
                    if p.status is ProposalStatus.ACTIVE]

    def all(self) -> list[Proposal]:
        with self._lock:
            return list(self._queue.values())

    def get(self, proposal_id: int) -> Proposal:
        with self._lock:
            return self._queue[proposal_id]

    def on_change(self, hook: Callable[[Proposal], None]) -> None:
        self._on_change.append(hook)

    # -- internal ------------------------------------------------------------

    def _fire(self, p: Proposal) -> None:
        for hook in self._on_change:
            try:
                hook(p)
            except Exception:
                pass
