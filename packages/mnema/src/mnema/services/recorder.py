from __future__ import annotations

from datetime import datetime
from typing import Any

from mnema.config import settings
from mnema.core.models import (
    Assertion,
    Confidence,
    OriginType,
    Provenance,
)


class MemoryRecorder:
    """Records, revises, and retracts assertions in the memory store."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def observe(
        self,
        agent_id: str,
        statement: str,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        confidence: float | None = None,
        source_id: str = "agent",
        namespace: str | None = None,
        derived_from: tuple[str, ...] = (),
        assertion_id: str | None = None,
    ) -> Assertion:
        """Build and record a new observed assertion.

        ``derived_from`` carries the assertion ids this observation depends
        on (I4: retraction cascade) — e.g. an outcome derives from the
        decision that produced it.
        """
        kw = {'id': assertion_id} if assertion_id else {}
        assertion = Assertion(
            **kw,
            agent_id=agent_id,
            namespace=namespace or settings.default_namespace,
            statement=statement,
            subject=subject,
            predicate=predicate,
            object=object,
            valid_from=valid_from,
            valid_to=valid_to,
            provenance=Provenance(
                origin_type=OriginType.OBSERVED,
                source_id=source_id,
                derived_from=tuple(derived_from),
            ),
            confidence=Confidence(
                value=confidence if confidence is not None else settings.default_confidence,
                method="empirical",
            ),
        )
        self._store.record(assertion)
        return assertion

    def revise(
        self,
        prior_id: str,
        *,
        statement: str,
        confidence: float | None = None,
        source_id: str = "agent",
    ) -> Assertion:
        """Supersede a prior assertion with a revised one."""
        prior = self._store.get(prior_id)
        if prior is None:
            raise KeyError(f"assertion not found: {prior_id!r}")
        revised = Assertion(
            agent_id=prior.agent_id,
            namespace=prior.namespace,
            statement=statement,
            subject=prior.subject,
            predicate=prior.predicate,
            object=prior.object,
            valid_from=prior.valid_from,
            valid_to=prior.valid_to,
            provenance=Provenance(origin_type=OriginType.OBSERVED, source_id=source_id),
            confidence=Confidence(
                value=confidence if confidence is not None else prior.confidence.value,
                method="empirical",
            ),
            status=prior.status,
            supersedes=prior.id,
        )
        self._store.record(revised)
        return revised

    def retract(self, assertion_id: str, *, source_id: str = "agent") -> Assertion:
        """Mark an assertion as retracted and auto-resolve any open contradictions it is part of."""
        assertion = self._store.get(assertion_id)
        if assertion is None:
            raise KeyError(f"assertion not found: {assertion_id!r}")
        retracted = assertion.retract(
            provenance=Provenance(origin_type=OriginType.OBSERVED, source_id=source_id)
        )
        self._store.record(retracted)
        # Auto-resolve graph contradictions where this assertion was a party
        try:
            from mnema.services.graph import KnowledgeGraph
            KnowledgeGraph(self._store).auto_resolve_on_retraction(assertion_id)
        except Exception:  # noqa: BLE001 — graph layer must never break retraction
            pass
        return retracted

    def cascade_retract(self, assertion_id: str, *, source_id: str = "agent") -> list[str]:
        """Retract assertion_id and surface all derived rule/skill ids that are now at risk.

        Rules and skills are not deleted — they remain in history. The caller should
        trigger re-consolidation to produce a clean digest.
        Returns list: [assertion_id] + at_risk_rule_and_skill_ids.
        """
        at_risk = self.retraction_cascade(assertion_id)
        self.retract(assertion_id, source_id=source_id)
        return [assertion_id] + at_risk

    def retraction_cascade(self, assertion_id: str) -> list[str]:
        """Return rule and skill ids that are at risk if assertion_id is retracted."""
        assertion = self._store.get(assertion_id)
        if assertion is None:
            return []
        rules = self._store.active_rules(
            namespace=assertion.namespace, agent_id=assertion.agent_id
        )
        skills = self._store.active_skills(
            namespace=assertion.namespace, agent_id=assertion.agent_id
        )
        at_risk_rules = [r.id for r in rules if assertion_id in r.derived_from]
        at_risk_skills = [s.id for s in skills if assertion_id in s.derived_from]
        return at_risk_rules + at_risk_skills
