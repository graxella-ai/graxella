"""Typed temporal graph layer for Mnema.

Nodes are existing objects (Assertion, Rule, Skill).
Edges are Relationship objects with typed semantics and bi-temporal validity.

Starting with one edge type — `contradicts` — which forces the resolution
workflow to be designed before the rest of the taxonomy is added.

Future edge types (not yet active):
  entails      A being true implies B is also true
  requires     A depends on B — used for multi-hop cascade
  deprecated_by  A (old) is replaced by B (new), stronger than supersedes
  exemplifies  A is a concrete example of abstract rule B
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RelationshipType(StrEnum):
    CONTRADICTS = "contradicts"
    # Reserved — uncomment as each is implemented:
    # ENTAILS     = "entails"
    # REQUIRES    = "requires"


class NodeType(StrEnum):
    ASSERTION = "assertion"
    RULE = "rule"
    SKILL = "skill"


class RelationshipStatus(StrEnum):
    OPEN = "open"                  # unresolved contradiction — agent should not proceed blindly
    RESOLVED = "resolved"          # explicitly resolved by a human or policy
    AUTO_RESOLVED = "auto_resolved"  # one side was retracted → contradiction dissolved


class Relationship(BaseModel):
    """An immutable typed edge between two nodes in the knowledge graph.

    Relationships are NOT deleted — they are resolved (status transitions).
    The full history of open → resolved transitions is auditable via the WAL.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: f"rel_{uuid.uuid4().hex}")
    edge_type: RelationshipType
    from_id: str
    from_type: NodeType
    to_id: str
    to_type: NodeType
    agent_id: str
    namespace: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.9)
    reason: str = Field(min_length=1)
    status: RelationshipStatus = RelationshipStatus.OPEN
    created_at: datetime = Field(default_factory=_utcnow)
    resolved_at: datetime | None = None
    resolution_note: str | None = None

    def resolve(
        self,
        *,
        note: str,
        auto: bool = False,
    ) -> Relationship:
        """Return a new Relationship with status=resolved (immutable transition)."""
        status = RelationshipStatus.AUTO_RESOLVED if auto else RelationshipStatus.RESOLVED
        return self.model_copy(
            update={
                "status": status,
                "resolved_at": _utcnow(),
                "resolution_note": note,
            }
        )


class ContradictionProposal(BaseModel):
    """A contradiction flagged by the LLM during sleep-phase consolidation."""

    model_config = ConfigDict(frozen=True)

    from_id: str
    to_id: str
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
