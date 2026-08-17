"""Mnema core domain model — FROZEN at v0.0 (see docs/adr/ADR-0001).

Design invariants (enforced, not documented-only):

  I1  Assertions are IMMUTABLE. Revision = new assertion + `supersedes` link.
  I2  Bi-temporal: `valid_from`/`valid_to` are event time (true in the world);
      `asserted_at` is transaction time (when Mnema learned it). Never conflate.
  I3  Identity is content-addressed: `content_hash` covers propositional
      content only — never confidence, status, id, or asserted_at.
  I4  Every assertion carries provenance. There is no anonymous belief.
  I5  This module imports ONLY pydantic + stdlib. No frameworks, no vendors.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "0.0"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return f"asr_{uuid.uuid4().hex}"


class OriginType(StrEnum):
    """Where a belief came from — the coarse trust taxonomy (I4)."""

    OBSERVED = "observed"            # agent perceived it directly (tool output, env)
    TOLD_BY_USER = "told_by_user"    # human said so
    TOLD_BY_AGENT = "told_by_agent"  # another agent said so
    INFERRED = "inferred"            # derived from other assertions
    EXTERNAL_DOC = "external_doc"    # read in a document / web page
    SYSTEM = "system"                # seeded / configured


class EpistemicStatus(StrEnum):
    """Lifecycle of a belief. Transitions create NEW assertions (I1)."""

    HYPOTHESIS = "hypothesis"
    BELIEF = "belief"
    ESTABLISHED = "established"
    DEPRECATED = "deprecated"
    RETRACTED = "retracted"


class Provenance(BaseModel):
    """Justification chain for an assertion."""

    model_config = ConfigDict(frozen=True)

    origin_type: OriginType
    source_id: str = Field(
        min_length=1,
        description="Stable identifier of the source: agent id, user id, doc URI, tool name.",
    )
    derived_from: tuple[str, ...] = Field(
        default=(),
        description="Assertion ids this belief was inferred from (I4: retraction cascade).",
    )
    method: str | None = Field(
        default=None, description="How it was produced, e.g. 'llm_extraction:qwen2.5'."
    )


class Confidence(BaseModel):
    """Calibrated-in-intent, heuristic-in-practice (v0). Kept out of content_hash."""

    model_config = ConfigDict(frozen=True)

    value: float = Field(ge=0.0, le=1.0)
    method: str = Field(default="unspecified", min_length=1)


class Assertion(BaseModel):
    """One propositional belief held by one agent, in one namespace, over one time window.

    The unit of memory in Mnema. Frozen (I1): mutation raises ValidationError.
    """

    model_config = ConfigDict(frozen=True)

    # -- identity -----------------------------------------------------------
    id: str = Field(default_factory=_new_id)
    schema_version: str = Field(default=SCHEMA_VERSION)
    namespace: str = Field(default="default", min_length=1)
    agent_id: str = Field(min_length=1, description="Which agent holds this belief.")

    # -- propositional content (hashed, I3) ----------------------------------
    statement: str = Field(min_length=1, description="Natural-language proposition.")
    subject: str | None = Field(default=None, description="Optional SPO triple: subject.")
    predicate: str | None = Field(default=None, description="Optional SPO triple: predicate.")
    object: str | None = Field(default=None, description="Optional SPO triple: object.")

    # -- bi-temporality (I2) --------------------------------------------------
    valid_from: datetime | None = Field(
        default=None, description="Event time: when this became true in the world."
    )
    valid_to: datetime | None = Field(
        default=None, description="Event time: when this stopped being true. None = open."
    )
    asserted_at: datetime = Field(
        default_factory=_utcnow, description="Transaction time: when Mnema recorded it."
    )

    # -- epistemics (NOT hashed) ----------------------------------------------
    provenance: Provenance
    confidence: Confidence
    status: EpistemicStatus = Field(default=EpistemicStatus.BELIEF)

    # -- revision chain (I1) ---------------------------------------------------
    supersedes: str | None = Field(
        default=None, description="Id of the assertion this one revises."
    )

    # ------------------------------------------------------------------ rules
    @field_validator("valid_from", "valid_to", "asserted_at")
    @classmethod
    def _tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetimes must be timezone-aware (UTC recommended)")
        return v

    @model_validator(mode="after")
    def _validity_window_ordered(self) -> Assertion:
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must be <= valid_to")
        return self

    # -------------------------------------------------------------- identity
    @property
    def content_hash(self) -> str:
        """sha256 over propositional content ONLY (I3). Deterministic."""
        canon = json.dumps(
            {
                "schema_version": self.schema_version,
                "namespace": self.namespace,
                "statement": self.statement,
                "subject": self.subject,
                "predicate": self.predicate,
                "object": self.object,
                "valid_from": self.valid_from.isoformat() if self.valid_from else None,
                "valid_to": self.valid_to.isoformat() if self.valid_to else None,
                "origin_type": self.provenance.origin_type.value,
                "source_id": self.provenance.source_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------- operations
    def revise(
        self,
        *,
        statement: str | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        confidence: Confidence | None = None,
        status: EpistemicStatus | None = None,
        provenance: Provenance | None = None,
    ) -> Assertion:
        """Belief revision (I1): returns a NEW assertion superseding this one."""
        return Assertion(
            namespace=self.namespace,
            agent_id=self.agent_id,
            statement=statement if statement is not None else self.statement,
            subject=self.subject,
            predicate=self.predicate,
            object=self.object,
            valid_from=valid_from if valid_from is not None else self.valid_from,
            valid_to=valid_to if valid_to is not None else self.valid_to,
            provenance=provenance if provenance is not None else self.provenance,
            confidence=confidence if confidence is not None else self.confidence,
            status=status if status is not None else self.status,
            supersedes=self.id,
        )

    def retract(self, *, provenance: Provenance | None = None) -> Assertion:
        """Convenience: revise into RETRACTED status."""
        return self.revise(status=EpistemicStatus.RETRACTED, provenance=provenance)
