"""Storage ports. Adapters implement these; the core never imports adapters.

This module is the enforcement point of the hexagonal boundary: FastAPI,
SQLite, Chroma, Neo4j — all of them live on the far side of these Protocols.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from mnema.core.consolidation import Digest, Rule, Skill
from mnema.core.events import Event
from mnema.core.models import Assertion


class EventLog(Protocol):
    """Append-only WAL. The source of truth."""

    def append(self, event: Event) -> int:
        """Append and return the monotonic sequence number."""
        ...

    def read(self, *, after_seq: int = 0, limit: int | None = None) -> Iterable[tuple[int, Event]]:
        """Yield (seq, event) pairs. limit=None means no cap — callers that need
        a cap must pass one explicitly. Never rely on a default cap for correctness."""
        ...


class AssertionRepository(Protocol):
    """Projection over the event log. Read-optimized view of current belief."""

    def record(self, assertion: Assertion) -> None:
        """Persist a new assertion AND append its event atomically."""
        ...

    def get(self, assertion_id: str) -> Assertion | None:
        ...

    def current_beliefs(
        self,
        *,
        namespace: str = "default",
        agent_id: str | None = None,
        subject: str | None = None,
        as_of: datetime | None = None,
    ) -> list[Assertion]:
        """Non-superseded, non-retracted assertions, optionally time-travelled
        to `as_of` (transaction time). This is the substrate of GET /believe."""
        ...


class Embedder(Protocol):
    """Embedding port (S1.4). Kept trivially narrow on purpose."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    @property
    def model_id(self) -> str:
        """Embeddings are versioned by model — re-index is detectable, not silent."""
        ...


class ConsolidationRepository(Protocol):
    """Projection over consolidation artifacts (S2.1).

    Adapters implementing this MUST:
      - Persist a `Digest` atomically with a `CONSOLIDATION_RUN` event
        (single transaction, mirroring `AssertionRepository.record`).
      - Preserve superseded rules/skills for time-travel: `get_digest(v)`
        returns the artifact set active at version `v`, not the latest.
      - Treat `mark_skill_outcome` as an in-place metric UPDATE — no event,
        no supersession (per ADR-0002).
    """

    def save_digest(self, digest: Digest) -> None:
        """Persist digest + rules + skills AND emit CONSOLIDATION_RUN atomically.

        For any rule/skill whose `derived_from` set overlaps a prior version's
        rule/skill on the same scope/name, the adapter sets `superseded_by` on
        the older row in the same transaction.
        """
        ...

    def latest_digest(
        self, *, namespace: str = "default", agent_id: str | None = None
    ) -> Digest | None:
        """Highest-version digest for the namespace/agent, or None if empty."""
        ...

    def get_digest(
        self, version: int, *, namespace: str = "default", agent_id: str | None = None
    ) -> Digest | None:
        """Digest at exactly `version`, hydrated with artifacts active at that
        version (created at or before `version`; not superseded at or before
        `version`)."""
        ...

    def active_rules(
        self,
        *,
        namespace: str = "default",
        agent_id: str | None = None,
        as_of_version: int | None = None,
    ) -> list[Rule]:
        """Non-superseded rules, optionally as-of a specific digest version."""
        ...

    def active_skills(
        self,
        *,
        namespace: str = "default",
        agent_id: str | None = None,
        as_of_version: int | None = None,
    ) -> list[Skill]:
        """Non-superseded skills, optionally as-of a specific digest version."""
        ...

    def mark_skill_outcome(self, skill_id: str, *, success: bool) -> None:
        """Atomically increment `success_count` or `failure_count`.

        NOT WAL-tracked: skill outcomes are metrics, not beliefs (ADR-0002).
        """
        ...
