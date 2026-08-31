"""Event envelope for Mnema's append-only log (the WAL).

The event log is the SOURCE OF TRUTH; the assertions table is a projection.
`/audit`, replay, and counterfactual rollback are queries over this log.

Core imports only pydantic + stdlib (I5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EventType(StrEnum):
    ASSERTION_RECORDED = "assertion.recorded"
    ASSERTION_SUPERSEDED = "assertion.superseded"
    ASSERTION_RETRACTED = "assertion.retracted"
    RETRIEVAL_PERFORMED = "retrieval.performed"
    CONSOLIDATION_RUN = "consolidation.run"
    # Graph layer — typed temporal edges
    RELATIONSHIP_ASSERTED = "relationship.asserted"
    RELATIONSHIP_RESOLVED = "relationship.resolved"


class Event(BaseModel):
    """Immutable envelope. `seq` is assigned by the log adapter on append."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    event_type: EventType
    occurred_at: datetime = Field(default_factory=_utcnow)
    namespace: str = "default"
    agent_id: str | None = None
    assertion_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
