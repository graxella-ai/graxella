"""ExperienceEpisode — one queryable row per agent execution.

The atomic unit of organisational intelligence. Every dispatch through the
runtime produces one episode with links back into the WAL, the assertion
store, and the decision ledger — so any episode can be replayed, cited,
retracted, or mined.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


def _new_id() -> str:
    return f"epi_{uuid.uuid4().hex}"


@dataclass
class ToolCall:
    """One tool invocation inside an episode."""
    tool_id: str
    args_hash: str
    ok: bool
    latency_ms: float
    err: str | None = None


@dataclass
class ExperienceEpisode:
    """Structured record of one dispatch. Immutable once written.

    Cross-references:
      - envelope_id     -> Envelope (AXON)
      - decisions       -> Decision ledger entries
      - evidence_ids    -> Mnema assertions written during the episode
      - context_ids     -> Mnema assertions read during the episode
    """
    id: str = field(default_factory=_new_id)
    session_id: str = ""
    envelope_id: str = ""

    # What was asked
    intent: str = ""
    task: str = ""
    skill: str | None = None
    side_effect_class: str = "read_only"

    # What was chosen
    agent_uri: str = ""
    routing_margin: float | None = None
    routing_flags: list[str] = field(default_factory=list)

    # What happened
    tool_calls: list[ToolCall] = field(default_factory=list)
    context_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)

    # How it ended
    ok: bool = False
    err: str | None = None
    latency_ms: float = 0.0
    retries: int = 0

    # Bookkeeping
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tool_calls"] = [asdict(tc) for tc in self.tool_calls]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


class ExperienceStore(Protocol):
    """Storage for Experience rows. First-cut is in-memory; production is SQLite."""

    def put(self, episode: ExperienceEpisode) -> None: ...
    def get(self, episode_id: str) -> ExperienceEpisode | None: ...
    def by_session(self, session_id: str) -> list[ExperienceEpisode]: ...
    def by_intent(self, intent: str) -> list[ExperienceEpisode]: ...
    def all(self) -> list[ExperienceEpisode]: ...


class InMemoryExperienceStore:
    """First-cut store — plain dict. Swap for SqliteExperienceStore in prod."""

    def __init__(self) -> None:
        self._rows: dict[str, ExperienceEpisode] = {}

    def put(self, episode: ExperienceEpisode) -> None:
        self._rows[episode.id] = episode

    def get(self, episode_id: str) -> ExperienceEpisode | None:
        return self._rows.get(episode_id)

    def by_session(self, session_id: str) -> list[ExperienceEpisode]:
        return [e for e in self._rows.values() if e.session_id == session_id]

    def by_intent(self, intent: str) -> list[ExperienceEpisode]:
        return [e for e in self._rows.values() if e.intent == intent]

    def all(self) -> list[ExperienceEpisode]:
        return list(self._rows.values())
