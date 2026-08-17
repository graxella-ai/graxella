from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mnema.config import settings
from mnema.core.events import EventType


class AuditService:
    """Audit and introspection queries over the WAL and projections."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def why_believed(self, assertion_id: str) -> dict:
        """Return full provenance and WAL history for an assertion."""
        assertion = self._store.get(assertion_id)
        if assertion is None:
            return {"error": "not found", "assertion_id": assertion_id}

        active_rules = self._store.active_rules(
            namespace=assertion.namespace, agent_id=assertion.agent_id
        )
        active_skills = self._store.active_skills(
            namespace=assertion.namespace, agent_id=assertion.agent_id
        )

        # Scan WAL to collect all events touching this assertion and to discover
        # whether it was later superseded (ASSERTION_SUPERSEDED payload has the new id).
        superseded_by_id: str | None = None
        wal_events = []
        for seq, evt in self._store.read():
            if evt.assertion_id != assertion_id:
                continue
            wal_events.append({
                "seq": seq,
                "type": evt.event_type.value,
                "occurred_at": evt.occurred_at.isoformat(),
            })
            if evt.event_type == EventType.ASSERTION_SUPERSEDED:
                superseded_by_id = evt.payload.get("superseded_by")

        return {
            "assertion_id": assertion_id,
            "assertion": assertion.model_dump(mode="json"),
            "provenance": assertion.provenance.model_dump(mode="json"),
            "status": assertion.status.value,
            "superseded_by": superseded_by_id,  # what replaced THIS assertion
            "derived_rules": [
                r.model_dump(mode="json")
                for r in active_rules
                if assertion_id in r.derived_from
            ],
            "derived_skills": [
                s.model_dump(mode="json")
                for s in active_skills
                if assertion_id in s.derived_from
            ],
            "wal_events": wal_events,
        }

    def belief_timeline(
        self,
        agent_id: str,
        subject: str,
        *,
        namespace: str = settings.default_namespace,
    ) -> list[dict]:
        """Ordered sequence of WAL events touching assertions with this subject."""
        matched: list[tuple[int, Any, Any]] = []
        seen_ids: set[str] = set()

        for seq, evt in self._store.read():
            if evt.agent_id != agent_id:
                continue
            if evt.assertion_id is None:
                continue
            assertion_id = evt.assertion_id
            if assertion_id in seen_ids:
                # Already resolved this assertion; include the event.
                a = self._store.get(assertion_id)
                if a is not None and a.subject == subject:
                    matched.append((seq, evt, a))
                continue
            a = self._store.get(assertion_id)
            if a is not None and a.subject == subject:
                seen_ids.add(assertion_id)
                matched.append((seq, evt, a))

        matched.sort(key=lambda t: t[0])
        return [
            {
                "seq": seq,
                "event": evt.event_type.value,
                "assertion_id": evt.assertion_id,
                "statement": a.statement if a is not None else None,
                "confidence": a.confidence.value if a is not None else None,
                "occurred_at": evt.occurred_at.isoformat(),
            }
            for seq, evt, a in matched
        ]

    def digest_diff(
        self,
        agent_id: str,
        v1: int,
        v2: int,
        *,
        namespace: str = settings.default_namespace,
    ) -> dict:
        """Compare two digest versions, reporting added/removed/retained artifacts."""
        d1 = self._store.get_digest(v1, namespace=namespace, agent_id=agent_id)
        d2 = self._store.get_digest(v2, namespace=namespace, agent_id=agent_id)
        if d1 is None or d2 is None:
            return {"error": "digest not found", "v1": v1, "v2": v2}

        v1_rule_ids = {r.id for r in d1.rules}
        v2_rule_ids = {r.id for r in d2.rules}
        v1_skill_ids = {s.id for s in d1.skills}
        v2_skill_ids = {s.id for s in d2.skills}

        return {
            "from_version": v1,
            "to_version": v2,
            "rules": {
                "added": [
                    r.model_dump(mode="json") for r in d2.rules if r.id not in v1_rule_ids
                ],
                "removed": [
                    r.model_dump(mode="json") for r in d1.rules if r.id not in v2_rule_ids
                ],
                "retained": [r.id for r in d2.rules if r.id in v1_rule_ids],
                "scope_updated": [
                    {"scope": r2.scope, "old_id": r1.id, "new_id": r2.id}
                    for r2 in d2.rules
                    for r1 in d1.rules
                    if r2.scope == r1.scope and r2.id != r1.id
                ],
            },
            "skills": {
                "added": [
                    s.model_dump(mode="json") for s in d2.skills if s.id not in v1_skill_ids
                ],
                "removed": [
                    s.model_dump(mode="json") for s in d1.skills if s.id not in v2_skill_ids
                ],
                "retained": [s.id for s in d2.skills if s.id in v1_skill_ids],
                "name_updated": [
                    {"name": s2.name, "old_id": s1.id, "new_id": s2.id}
                    for s2 in d2.skills
                    for s1 in d1.skills
                    if s2.name == s1.name and s2.id != s1.id
                ],
            },
        }

    def compliance_report(
        self,
        agent_id: str,
        *,
        namespace: str = settings.default_namespace,
    ) -> dict:
        """Snapshot report of active memory state and supersession history."""
        all_beliefs = self._store.current_beliefs(namespace=namespace, agent_id=agent_id)
        active_rules = self._store.active_rules(namespace=namespace, agent_id=agent_id)
        active_skills = self._store.active_skills(namespace=namespace, agent_id=agent_id)
        latest = self._store.latest_digest(namespace=namespace, agent_id=agent_id)
        supersession_chains: list[dict] = []
        total_wal_events = 0
        for _seq, evt in self._store.read():
            total_wal_events += 1
            if evt.event_type == EventType.ASSERTION_SUPERSEDED and evt.agent_id == agent_id:
                a = self._store.get(evt.assertion_id) if evt.assertion_id else None
                supersession_chains.append(
                    {
                        "original": evt.assertion_id,
                        "superseded_by": evt.payload.get("superseded_by"),
                        "subject": a.subject if a is not None else None,
                    }
                )

        return {
            "agent_id": agent_id,
            "namespace": namespace,
            "generated_at": datetime.now(UTC).isoformat(),
            "total_assertions_active": len(all_beliefs),
            "total_wal_events": total_wal_events,
            "digest_versions": latest.version if latest is not None else 0,
            "active_rules": [r.model_dump(mode="json") for r in active_rules],
            "active_skills": [s.model_dump(mode="json") for s in active_skills],
            "supersession_chains": supersession_chains,
        }
