"""graxella.gate.spec — the Promotion Spec (v0.1), as executable schema.

One typed lifecycle for every learnable artifact: prompt, transform,
route weight, tool binding, playbook delta, model tier, disclosure
summary. Design doc: docs/specs/promotion-spec.md — the doc is binding;
this module is its executable form.

Invariants enforced here (not merely documented):

  I1  Proposals are immutable. A lifecycle transition returns a NEW
      validated instance via ``with_status``; the old object is the
      audit record.
  I2  Nothing reaches APPROVED or ACTIVE without at least one
      EvidenceCitation. Human sign-off is recorded as a citation
      (role=operator_decision), never as an exemption.
  I3  Transitions follow the state machine. Anything else raises
      InvalidTransitionError.
  I4  The gate tuple includes ``model_id`` — learned behavior is scoped
      per model, which is what makes graxella LLM-agnostic.

This module imports pydantic + stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from graxella.exceptions import GraxellaError

SPEC_VERSION = "0.1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _random_id() -> str:
    return f"prop_{uuid.uuid4().hex[:16]}"


class InvalidTransitionError(GraxellaError):
    """Raised when a lifecycle transition violates the state machine."""


class ArtifactKind(str, Enum):
    """What a proposal changes. Payload shapes per kind live in the spec doc."""

    PROMPT = "prompt"
    TRANSFORM = "transform"
    ROUTE_WEIGHT = "route_weight"
    TOOL_BINDING = "tool_binding"
    PLAYBOOK = "playbook"
    MODEL_TIER = "model_tier"
    DISCLOSURE_SUMMARY = "disclosure_summary"
    RULE = "rule"              # legacy rulebook substitution (migrates to tool_binding)
    TRUST_TIER = "trust_tier"
    SKILL_TAGS = "skill_tags"


class BlastRadius(str, Enum):
    """Safety envelope, not a score. Wide blast requires overwhelming
    same-tuple evidence or human sign-off (gate's job, Phase 1)."""

    NARROW = "narrow"
    WIDE = "wide"
    UNKNOWN = "unknown"


class ProposalStatus(str, Enum):
    PENDING = "pending"
    NEEDS_HUMAN = "needs_human"
    APPROVED = "approved"      # gate passed; not yet live
    ACTIVE = "active"          # live — this IS a promotion
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"
    SUPERSEDED = "superseded"


#: The state machine. Absence from this table means the transition is invalid.
TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.PENDING: frozenset({
        ProposalStatus.NEEDS_HUMAN, ProposalStatus.APPROVED, ProposalStatus.REJECTED,
    }),
    ProposalStatus.NEEDS_HUMAN: frozenset({
        ProposalStatus.APPROVED, ProposalStatus.REJECTED,
    }),
    ProposalStatus.APPROVED: frozenset({
        ProposalStatus.ACTIVE, ProposalStatus.REJECTED,
    }),
    ProposalStatus.ACTIVE: frozenset({
        ProposalStatus.ROLLED_BACK, ProposalStatus.SUPERSEDED,
    }),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.ROLLED_BACK: frozenset(),
    ProposalStatus.SUPERSEDED: frozenset(),
}


class EvidenceRole(str, Enum):
    PRIOR_OUTCOME = "prior_outcome"
    PAIRED_REPLAY = "paired_replay"
    OPERATOR_DECISION = "operator_decision"
    CONSTITUTION_CHECK = "constitution_check"
    EPISODE = "episode"
    DOC = "doc"


class EvidenceCitation(BaseModel):
    """One citation into the ledger. Every promotion cites — no exceptions."""

    model_config = ConfigDict(frozen=True)

    assertion_id: str = Field(min_length=1)
    role: EvidenceRole
    note: str = ""


class TargetScope(BaseModel):
    """Where an artifact applies. ``domain`` is mandatory — evidence never
    leaks across domains. ``model_id`` scopes learned behavior per model."""

    model_config = ConfigDict(frozen=True)

    domain: str = Field(min_length=1)
    agent: Optional[str] = None
    skill: Optional[str] = None
    tool: Optional[str] = None
    model_id: Optional[str] = None

    def gate_key(self, kind: ArtifactKind) -> tuple:
        """The Evidence Gate's prior-lookup key."""
        return (self.domain, kind.value, self.agent, self.skill,
                self.tool, self.model_id)


class Proposal(BaseModel):
    """One proposed behavior change — the unit of improvement.

    Frozen (I1). Use ``with_status`` for lifecycle moves and
    ``deterministic_id`` for miner-emitted proposals so identical evidence
    yields identical ids across re-mines.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=_random_id)
    spec_version: str = Field(default=SPEC_VERSION)
    kind: ArtifactKind
    target: TargetScope
    payload: dict[str, Any] = Field(default_factory=dict)
    origin: str = Field(min_length=1, description="miner:<name> or operator:<name>")
    blast_radius: BlastRadius = BlastRadius.UNKNOWN
    evidence: tuple[EvidenceCitation, ...] = ()
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    status: ProposalStatus = ProposalStatus.PENDING
    version: int = Field(default=1, ge=1)
    supersedes: Optional[str] = None
    rollback_of: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    note: str = ""

    # -- invariants ---------------------------------------------------------

    @model_validator(mode="after")
    def _evidence_required_for_promotion(self) -> "Proposal":
        if self.status in (ProposalStatus.APPROVED, ProposalStatus.ACTIVE) \
                and not self.evidence:
            raise ValueError(
                f"status={self.status.value} requires at least one "
                "EvidenceCitation (I2: every promotion cites; operator "
                "sign-off is a citation with role=operator_decision)"
            )
        return self

    # -- lifecycle ----------------------------------------------------------

    def with_status(self, new_status: ProposalStatus, *, by: str,
                    note: str = "",
                    extra_evidence: tuple[EvidenceCitation, ...] = (),
                    ) -> "Proposal":
        """Return a NEW validated Proposal in ``new_status``.

        Validates the transition against TRANSITIONS (I3) and re-runs all
        model invariants (so an APPROVED result without evidence still
        fails, I2). The receiver is untouched — it remains the audit
        record of the previous state.
        """
        if new_status not in TRANSITIONS.get(self.status, frozenset()):
            raise InvalidTransitionError(
                f"invalid transition {self.status.value} -> {new_status.value} "
                f"for proposal {self.id}"
            )
        data = self.model_dump()
        data.update(
            status=new_status,
            decided_at=_utcnow(),
            decided_by=by,
            note=note or self.note,
            evidence=tuple(self.evidence) + tuple(extra_evidence),
        )
        return Proposal.model_validate(data)

    # -- identity -----------------------------------------------------------

    @classmethod
    def deterministic_id(cls, kind: ArtifactKind, target: TargetScope,
                         payload: dict[str, Any]) -> str:
        """Stable id from semantic identity: same evidence ⇒ same id, so
        re-mining never dangles promote-by-id references or audit links."""
        basis = json.dumps(
            {"kind": kind.value, "target": target.model_dump(),
             "payload": payload},
            sort_keys=True, default=str,
        )
        return f"prop_{hashlib.sha256(basis.encode()).hexdigest()[:16]}"

    # -- rendering ----------------------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe rendering for tracer events and (Phase 1) ledger writes."""
        return self.model_dump(mode="json")


__all__ = [
    "SPEC_VERSION",
    "ArtifactKind",
    "BlastRadius",
    "ProposalStatus",
    "TRANSITIONS",
    "EvidenceRole",
    "EvidenceCitation",
    "TargetScope",
    "Proposal",
    "InvalidTransitionError",
]
