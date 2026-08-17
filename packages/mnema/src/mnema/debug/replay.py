from __future__ import annotations

from typing import Any


class EventReplayer:
    """WAL forensics — inspect, filter, and replay event sequences."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def events_in_range(self, start_seq: int, end_seq: int) -> list[dict]:
        """Return all WAL events whose sequence number falls in [start_seq, end_seq]."""
        return [
            {
                "seq": s,
                "event_id": e.event_id,
                "type": e.event_type.value,
                "namespace": e.namespace,
                "agent_id": e.agent_id,
                "assertion_id": e.assertion_id,
                "payload": e.payload,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for s, e in self._store.read()  # limit=None — full WAL
            if start_seq <= s <= end_seq
        ]

    def events_for_agent(
        self,
        agent_id: str,
        *,
        namespace: str = "default",
    ) -> list[dict]:
        """Return all WAL events for a given agent in a namespace."""
        return [
            {
                "seq": s,
                "event_id": e.event_id,
                "type": e.event_type.value,
                "namespace": e.namespace,
                "agent_id": e.agent_id,
                "assertion_id": e.assertion_id,
                "payload": e.payload,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for s, e in self._store.read()
            if e.agent_id == agent_id and e.namespace == namespace
        ]

    def consolidation_history(
        self,
        agent_id: str,
        *,
        namespace: str = "default",
    ) -> list[dict]:
        """Return only CONSOLIDATION_RUN events for this agent."""
        from mnema.core.events import EventType

        return [
            {
                "seq": s,
                "version": e.payload.get("version"),
                "source_seq_range": e.payload.get("source_seq_range"),
                "rule_count": len(e.payload.get("rule_ids", [])),
                "skill_count": len(e.payload.get("skill_ids", [])),
                "occurred_at": e.occurred_at.isoformat(),
            }
            for s, e in self._store.read()
            if e.event_type == EventType.CONSOLIDATION_RUN
            and e.agent_id == agent_id
            and e.namespace == namespace
        ]

    def replay_beliefs_at_seq(
        self,
        agent_id: str,
        target_seq: int,
        *,
        namespace: str = "default",
    ) -> list[dict]:
        """Reconstruct which assertions were active up to (and including) target_seq.

        Correctness guarantee: uses WAL events only to determine the active ID set,
        then fetches immutable assertion data. Retracted assertions are filtered out
        because a retraction records a new assertion with status='retracted' whose
        WAL event is ASSERTION_RECORDED — we explicitly exclude those.
        """
        from mnema.core.events import EventType

        active: set[str] = set()
        for s, e in self._store.read():
            if s > target_seq:
                break
            if e.agent_id != agent_id or e.namespace != namespace:
                continue
            if e.event_type == EventType.ASSERTION_RECORDED and e.assertion_id:
                active.add(e.assertion_id)
            elif e.event_type in (EventType.ASSERTION_SUPERSEDED, EventType.ASSERTION_RETRACTED):
                active.discard(e.assertion_id or "")

        result: list[dict] = []
        for aid in active:
            a = self._store.get(aid)
            if a is None:
                continue
            # Filter out retracted assertions: a retraction creates a new assertion
            # with status='retracted' whose ASSERTION_RECORDED event adds it to the
            # active set. We must exclude it — it is not a current belief.
            if a.status.value == "retracted":
                continue
            result.append(
                {
                    "id": a.id,
                    "statement": a.statement,
                    "subject": a.subject,
                    "confidence": a.confidence.value,
                    "status": a.status.value,
                }
            )
        return result
