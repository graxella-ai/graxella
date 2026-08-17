from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class BeliefInspector:
    """Rich introspection of agent memory state."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def snapshot(self, agent_id: str, *, namespace: str = "default") -> dict:
        """Return a full snapshot of current memory state for an agent."""
        beliefs = self._store.current_beliefs(namespace=namespace, agent_id=agent_id)
        rules = self._store.active_rules(namespace=namespace, agent_id=agent_id)
        skills = self._store.active_skills(namespace=namespace, agent_id=agent_id)
        latest = self._store.latest_digest(namespace=namespace, agent_id=agent_id)
        wal_count = sum(1 for _ in self._store.read())
        return {
            "agent_id": agent_id,
            "namespace": namespace,
            "captured_at": datetime.now(UTC).isoformat(),
            "active_beliefs": [
                {
                    "id": a.id,
                    "subject": a.subject,
                    "statement": a.statement,
                    "confidence": a.confidence.value,
                    "status": a.status.value,
                    "asserted_at": a.asserted_at.isoformat(),
                }
                for a in beliefs
            ],
            "active_rules": [
                {
                    "id": r.id,
                    "scope": r.scope,
                    "text": r.text,
                    "confidence": r.confidence,
                    "digest_version": r.digest_version,
                }
                for r in rules
            ],
            "active_skills": [
                {
                    "id": s.id,
                    "name": s.name,
                    "signature": s.signature,
                    "success_count": s.success_count,
                    "failure_count": s.failure_count,
                }
                for s in skills
            ],
            "latest_digest_version": latest.version if latest else None,
            "total_wal_events": wal_count,
        }

    def trace(self, assertion_id: str, *, namespace: str = "default") -> dict:
        """Trace the full history of a single assertion including derived artifacts."""
        a = self._store.get(assertion_id)
        ns = a.namespace if a else namespace
        aid = a.agent_id if a else None
        rules = self._store.active_rules(namespace=ns, agent_id=aid) if aid else []
        skills = self._store.active_skills(namespace=ns, agent_id=aid) if aid else []
        return {
            "assertion_id": assertion_id,
            "found": a is not None,
            "assertion": a.model_dump(mode="json") if a else None,
            "wal_events": [
                {
                    "seq": s,
                    "type": e.event_type.value,
                    "occurred_at": e.occurred_at.isoformat(),
                }
                for s, e in self._store.read()
                if e.assertion_id == assertion_id
            ],
            "derived_rules_at_risk": [r.id for r in rules if assertion_id in r.derived_from],
            "derived_skills_at_risk": [s.id for s in skills if assertion_id in s.derived_from],
        }

    def active_at(
        self,
        agent_id: str,
        dt: datetime,
        *,
        namespace: str = "default",
    ) -> list[dict]:
        """Return beliefs that were active as of the given datetime."""
        beliefs = self._store.current_beliefs(namespace=namespace, agent_id=agent_id, as_of=dt)
        return [
            {
                "id": a.id,
                "statement": a.statement,
                "subject": a.subject,
                "confidence": a.confidence.value,
                "asserted_at": a.asserted_at.isoformat(),
            }
            for a in beliefs
        ]

    def orphaned_rules(self, agent_id: str, *, namespace: str = "default") -> list[dict]:
        """Return rules whose source assertions are no longer active."""
        current_ids = {
            a.id
            for a in self._store.current_beliefs(namespace=namespace, agent_id=agent_id)
        }
        rules = self._store.active_rules(namespace=namespace, agent_id=agent_id)
        return [
            r.model_dump(mode="json")
            for r in rules
            if not any(sid in current_ids for sid in r.derived_from)
        ]
