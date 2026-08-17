"""graxella.gate.promoter — DEPRECATED migration shim (one release).

The scored ``GatePolicy`` — hand-tuned weights over quality/compliance/
cost/latency — was the explicitly rejected gate design. It is DELETED
(task 1-5): importing the name still works, constructing it raises,
pointing to the replacement:

    from graxella.gate import EvidenceGate      # the real gate
    from graxella.gate.spec import Proposal     # the one Proposal type

``PromotionGate`` remains, one release, as a plain human-review queue
(propose → approve → activate, nothing automatic) for old dashboards and
examples; it emits a DeprecationWarning on construction. New code uses
EvidenceGate + spec.Proposal exclusively.
"""
from __future__ import annotations

import itertools
import time
import warnings
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Callable


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"   # human passed it but not yet active
    ACTIVE = "active"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class GateDecision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    NEEDS_HUMAN = "needs_human"
    AUTO_REJECT = "auto_reject"


def GatePolicy(*_args: Any, **_kwargs: Any) -> Any:  # noqa: N802 — legacy name
    raise RuntimeError(
        "GatePolicy was the rejected scored-gate design and has been "
        "removed (build plan task 1-5). Use graxella.gate.EvidenceGate — "
        "decisions come from ledger evidence, not weighted opinions."
    )


def ObjectiveScores(*_args: Any, **_kwargs: Any) -> Any:  # noqa: N802 — legacy name
    raise RuntimeError(
        "ObjectiveScores belonged to the removed scored gate (task 1-5). "
        "Evidence for a proposal is cited ledger outcomes — see "
        "graxella.gate.EvidenceGate and docs/specs/promotion-spec.md."
    )


@dataclass
class Proposal:
    """Legacy queue item. Deprecated — see graxella.gate.spec.Proposal."""

    id: int
    kind: str
    payload: dict
    score: float = 0.0
    blast_radius: str = "unknown"
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    decided_by: str | None = None
    note: str | None = None


class PromotionGate:
    """DEPRECATED human-review queue. Nothing automatic remains: every
    proposal waits for an explicit approve/reject. The Evidence Gate is
    the replacement for anything smarter."""

    def __init__(self, *, threshold: float | None = None,
                 require_human: bool = True, policy: Any = None) -> None:
        warnings.warn(
            "PromotionGate is deprecated (task 1-5): use "
            "graxella.gate.EvidenceGate with graxella.gate.spec.Proposal.",
            DeprecationWarning, stacklevel=2,
        )
        if policy is not None:
            raise RuntimeError("GatePolicy support was removed (task 1-5); "
                               "use graxella.gate.EvidenceGate")
        del threshold, require_human  # accepted for import-compat only
        self._queue: dict[int, Proposal] = {}
        self._seq = itertools.count(1)
        self._lock = Lock()
        self._on_change: list[Callable[[Proposal], None]] = []

    # -- API -----------------------------------------------------------------

    def propose(self, kind: str, payload: dict, *, score: float = 0.0,
                blast_radius: str = "unknown", **_ignored: Any) -> Proposal:
        with self._lock:
            p = Proposal(id=next(self._seq), kind=kind, payload=dict(payload),
                         score=float(score), blast_radius=blast_radius)
            self._queue[p.id] = p
        self._fire(p)
        return p

    def approve(self, proposal_id: int, *, by: str, note: str = "") -> Proposal:
        return self._transition(proposal_id, ProposalStatus.APPROVED, by, note)

    def activate(self, proposal_id: int, *, by: str) -> Proposal:
        with self._lock:
            p = self._queue[proposal_id]
            if p.status is not ProposalStatus.APPROVED:
                raise ValueError(f"proposal {proposal_id} not approved yet")
        return self._transition(proposal_id, ProposalStatus.ACTIVE, by, "")

    def reject(self, proposal_id: int, *, by: str, note: str = "") -> Proposal:
        return self._transition(proposal_id, ProposalStatus.REJECTED, by, note)

    def rollback(self, proposal_id: int, *, by: str, note: str = "") -> Proposal:
        return self._transition(proposal_id, ProposalStatus.ROLLED_BACK, by, note)

    def evaluate(self, proposal_id: int) -> GateDecision:
        """Legacy hook: everything needs a human now."""
        del proposal_id
        return GateDecision.NEEDS_HUMAN

    def auto_evaluate(self, proposal_id: int, **_kw: Any) -> tuple[GateDecision, Proposal]:
        return GateDecision.NEEDS_HUMAN, self.get(proposal_id)

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

    def _transition(self, proposal_id: int, status: ProposalStatus,
                    by: str, note: str) -> Proposal:
        with self._lock:
            p = self._queue[proposal_id]
            p.status = status
            p.decided_at = time.time()
            p.decided_by = by
            p.note = note or p.note
        self._fire(p)
        return p

    def _fire(self, p: Proposal) -> None:
        for hook in self._on_change:
            try:
                hook(p)
            except Exception:
                pass
