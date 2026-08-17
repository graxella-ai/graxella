"""KnowledgeGraph service — typed temporal edges over the assertion store.

Exposes the contradiction workflow:
  1. assert_contradicts()  — record that two assertions conflict
  2. open_contradictions()  — surface all unresolved conflicts for an agent
  3. resolve()              — mark a contradiction resolved (human or policy)
  4. auto_resolve_on_retraction() — called by MemoryRecorder when an assertion is retracted

The graph is a projection over the WAL — every state change emits an event.
"""

from __future__ import annotations

import logging
from typing import Any

from mnema.config import settings
from mnema.core.graph import (
    ContradictionProposal,
    NodeType,
    Relationship,
    RelationshipStatus,
    RelationshipType,
)

log = logging.getLogger(__name__)


class KnowledgeGraph:
    """Manages typed relationships between memory nodes."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def assert_contradicts(
        self,
        agent_id: str,
        from_id: str,
        to_id: str,
        *,
        reason: str,
        confidence: float = 0.9,
        namespace: str = settings.default_namespace,
    ) -> Relationship:
        """Declare that assertion `from_id` contradicts assertion `to_id`.

        Both assertions must exist. The relationship is stored as OPEN —
        the agent should not act on conflicting beliefs until resolved.
        Returns the created Relationship.
        """
        self._require_assertion(from_id)
        self._require_assertion(to_id)

        rel = Relationship(
            edge_type=RelationshipType.CONTRADICTS,
            from_id=from_id,
            from_type=NodeType.ASSERTION,
            to_id=to_id,
            to_type=NodeType.ASSERTION,
            agent_id=agent_id,
            namespace=namespace,
            confidence=confidence,
            reason=reason,
        )
        self._store.assert_relationship(rel)
        log.info(
            "Contradiction asserted agent=%r %s <-> %s: %r",
            agent_id, from_id[:8], to_id[:8], reason,
        )
        return rel

    def assert_from_proposals(
        self,
        agent_id: str,
        proposals: list[ContradictionProposal],
        *,
        namespace: str = settings.default_namespace,
    ) -> list[Relationship]:
        """Persist a batch of LLM-detected contradiction proposals.

        Silently skips proposals where either assertion no longer exists
        (may have been retracted between consolidation and write).
        """
        created: list[Relationship] = []
        for p in proposals:
            from_a = self._store.get(p.from_id)
            to_a = self._store.get(p.to_id)
            if from_a is None or to_a is None:
                log.warning(
                    "Skipping contradiction proposal: assertion not found "
                    "from_id=%r to_id=%r", p.from_id, p.to_id,
                )
                continue
            rel = Relationship(
                edge_type=RelationshipType.CONTRADICTS,
                from_id=p.from_id,
                from_type=NodeType.ASSERTION,
                to_id=p.to_id,
                to_type=NodeType.ASSERTION,
                agent_id=from_a.agent_id,
                namespace=from_a.namespace,
                confidence=p.confidence,
                reason=p.reason,
            )
            self._store.assert_relationship(rel)
            created.append(rel)
        return created

    def open_contradictions(
        self,
        agent_id: str,
        *,
        namespace: str = settings.default_namespace,
    ) -> list[dict]:
        """Return all open contradictions for an agent, enriched with statement text."""
        rels = self._store.open_relationships(
            agent_id, namespace=namespace, edge_type=RelationshipType.CONTRADICTS
        )
        result = []
        for r in rels:
            from_a = self._store.get(r.from_id)
            to_a = self._store.get(r.to_id)
            result.append(
                {
                    "relationship_id": r.id,
                    "from_id": r.from_id,
                    "from_statement": from_a.statement if from_a else "[retracted]",
                    "to_id": r.to_id,
                    "to_statement": to_a.statement if to_a else "[retracted]",
                    "reason": r.reason,
                    "confidence": r.confidence,
                    "created_at": r.created_at.isoformat(),
                }
            )
        return result

    def resolve(
        self,
        relationship_id: str,
        *,
        note: str,
    ) -> Relationship:
        """Explicitly resolve a contradiction (human decision or policy rule).

        `note` should state which assertion prevails and why — this becomes
        the permanent audit trail for why the conflict was resolved.
        """
        rel = self._store.resolve_relationship(relationship_id, note=note, auto=False)
        log.info("Contradiction resolved id=%r note=%r", relationship_id, note)
        return rel

    def auto_resolve_on_retraction(self, assertion_id: str) -> list[str]:
        """Auto-resolve all open contradictions involving a retracted assertion.

        Called by MemoryRecorder.retract() — when one side of a contradiction
        is retracted, the conflict dissolves automatically.
        Returns list of relationship IDs that were auto-resolved.
        """
        rels = self._store.relationships_involving(assertion_id)
        resolved_ids: list[str] = []
        for r in rels:
            if r.status == RelationshipStatus.OPEN:
                self._store.resolve_relationship(
                    r.id,
                    note=f"Auto-resolved: assertion {assertion_id[:8]}... was retracted.",
                    auto=True,
                )
                resolved_ids.append(r.id)
                log.info(
                    "Auto-resolved contradiction id=%r on retraction of %r",
                    r.id, assertion_id,
                )
        return resolved_ids

    def contradiction_summary(
        self,
        agent_id: str,
        *,
        namespace: str = settings.default_namespace,
    ) -> dict:
        """Summary of contradiction state for compliance reporting."""
        all_rels = self._store.open_relationships(
            agent_id, namespace=namespace, edge_type=RelationshipType.CONTRADICTS
        )
        return {
            "agent_id": agent_id,
            "open_contradictions": len(all_rels),
            "knowledge_integrity": "clean" if not all_rels else "conflicts_present",
        }

    def _require_assertion(self, assertion_id: str) -> None:
        if self._store.get(assertion_id) is None:
            raise KeyError(f"assertion not found: {assertion_id!r}")
