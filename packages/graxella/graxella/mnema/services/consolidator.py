from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from graxella.mnema.config import settings
from graxella.mnema.core.consolidation import Digest, Rule, Skill
from graxella.mnema.core.events import EventType
from graxella.mnema.ports.llm import LLMCallError

log = logging.getLogger(__name__)


class SleepConsolidator:
    """Runs the sleep-phase consolidation loop: beliefs -> rules + skills -> digest."""

    def __init__(self, store: Any, llm: Any) -> None:
        self._store = store
        self._llm = llm

    def consolidate(
        self,
        agent_id: str,
        *,
        namespace: str = settings.default_namespace,
        max_rules: int = 10,
        max_skills: int = 5,
    ) -> Digest | None:
        """Consolidate current beliefs into a new digest. Returns None if nothing to do."""
        # 1. Scan WAL for last consolidation run for this agent/namespace.
        last_seq: int = 0
        last_consolidation_time = None
        current_max_seq: int = 0

        for seq, evt in self._store.read():
            current_max_seq = max(current_max_seq, seq)
            if (
                evt.event_type == EventType.CONSOLIDATION_RUN
                and evt.agent_id == agent_id
                and evt.namespace == namespace
                and seq > last_seq
            ):
                last_seq = seq
                last_consolidation_time = evt.occurred_at

        if current_max_seq == 0:
            return None

        # 2. Collect current beliefs.
        all_beliefs = self._store.current_beliefs(namespace=namespace, agent_id=agent_id)
        if len(all_beliefs) < 2:
            return None

        # 3. Filter beliefs to those added since last consolidation.
        if last_seq == 0 or last_consolidation_time is None:
            beliefs = all_beliefs
        else:
            beliefs = [a for a in all_beliefs if a.asserted_at > last_consolidation_time]
            if not beliefs:
                beliefs = all_beliefs

        # 4. Serialise beliefs for the LLM.
        dicts = [
            {
                "id": a.id,
                "statement": a.statement,
                "subject": a.subject,
                "confidence": a.confidence.value,
            }
            for a in beliefs
        ]

        # 5. Ask LLM for rule, skill proposals, and contradiction detection.
        # Abort consolidation entirely if LLM fails — returning empty proposals
        # would overwrite existing rules with nothing.
        try:
            rule_proposals = self._llm.extract_rules(dicts, max_rules=max_rules)
            skill_proposals = self._llm.extract_skills(dicts, max_skills=max_skills)
            contradiction_proposals = self._llm.detect_contradictions(dicts)
        except LLMCallError as exc:
            log.error(
                "Consolidation aborted for agent=%r namespace=%r: LLM call failed: %s",
                agent_id, namespace, exc,
            )
            return None

        # 5b. Persist detected contradictions via KnowledgeGraph service.
        if contradiction_proposals:
            from graxella.mnema.services.graph import KnowledgeGraph
            new_rels = KnowledgeGraph(self._store).assert_from_proposals(
                agent_id, contradiction_proposals, namespace=namespace
            )
            if new_rels:
                log.warning(
                    "Consolidation detected %d contradiction(s) for agent=%r — "
                    "resolve via KnowledgeGraph.resolve() before acting.",
                    len(new_rels), agent_id,
                )

        # 6. Determine next digest version.
        existing = self._store.latest_digest(namespace=namespace, agent_id=agent_id)
        next_version = (existing.version + 1) if existing is not None else 1

        # 7. Build Rule objects from proposals, skipping invalid ones.
        rules: list[Rule] = []
        for proposal in rule_proposals:
            try:
                rules.append(
                    Rule(
                        text=proposal.text,
                        scope=proposal.scope,
                        confidence=proposal.confidence,
                        derived_from=proposal.derived_from,
                        digest_version=next_version,
                    )
                )
            except (ValidationError, ValueError) as exc:
                log.warning("Skipping invalid rule proposal: %s", exc)

        # 8. Build Skill objects from proposals, skipping invalid ones.
        skills: list[Skill] = []
        for proposal in skill_proposals:
            try:
                skills.append(
                    Skill(
                        name=proposal.name,
                        description=proposal.description,
                        code=proposal.code,
                        signature=proposal.signature,
                        derived_from=proposal.derived_from,
                        digest_version=next_version,
                    )
                )
            except (ValidationError, ValueError) as exc:
                log.warning("Skipping invalid skill proposal: %s", exc)

        # 9. Build and persist the digest.
        digest = Digest(
            version=next_version,
            namespace=namespace,
            agent_id=agent_id,
            rules=tuple(rules),
            skills=tuple(skills),
            source_event_seq_range=(last_seq, current_max_seq),
        )
        self._store.save_digest(digest)
        return digest
