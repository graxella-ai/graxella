"""SQLite adapter: SQLModel tables + a repository satisfying the storage ports.

Unit-of-work discipline: `record()` writes the event and the projection row in
ONE transaction. If either fails, neither happened.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import update
from sqlmodel import Field, Session, SQLModel, create_engine, select

from graxella.mnema.core.consolidation import Digest, Rule, Skill
from graxella.mnema.core.events import Event, EventType
from graxella.mnema.core.graph import Relationship, RelationshipStatus
from graxella.mnema.core.models import Assertion


# ---------------------------------------------------------------- tables ----
class EventRow(SQLModel, table=True):
    __tablename__ = "events"

    seq: int | None = Field(default=None, primary_key=True)  # monotonic WAL seq
    event_id: str = Field(index=True, unique=True)
    event_type: str = Field(index=True)
    occurred_at: datetime = Field(index=True)
    namespace: str = Field(index=True)
    agent_id: str | None = Field(default=None, index=True)
    assertion_id: str | None = Field(default=None, index=True)
    payload_json: str = "{}"


class AssertionRow(SQLModel, table=True):
    __tablename__ = "assertions"

    id: str = Field(primary_key=True)
    schema_version: str
    namespace: str = Field(index=True)
    agent_id: str = Field(index=True)
    statement: str
    subject: str | None = Field(default=None, index=True)
    predicate: str | None = None
    object: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    asserted_at: datetime = Field(index=True)
    provenance_json: str
    confidence_json: str
    status: str = Field(index=True)
    supersedes: str | None = Field(default=None, index=True)
    superseded_by: str | None = Field(default=None, index=True)  # denormalized for reads
    content_hash: str = Field(index=True)


class DigestRow(SQLModel, table=True):
    __tablename__ = "digests"

    # Composite PK by (namespace, agent_id, version) — but SQLModel single-PK is
    # simplest; we synthesize a surrogate key.
    pk: int | None = Field(default=None, primary_key=True)
    schema_version: str
    version: int = Field(index=True)
    namespace: str = Field(index=True)
    agent_id: str = Field(index=True)
    source_seq_start: int
    source_seq_end: int
    created_at: datetime = Field(index=True)


class RuleRow(SQLModel, table=True):
    __tablename__ = "rules"

    id: str = Field(primary_key=True)
    schema_version: str
    namespace: str = Field(index=True)
    agent_id: str = Field(index=True)
    text: str
    scope: str = Field(index=True)
    derived_from_json: str
    confidence: float
    digest_version: int = Field(index=True)
    created_at: datetime
    superseded_by: str | None = Field(default=None, index=True)
    superseded_at_version: int | None = Field(default=None, index=True)


class SkillRow(SQLModel, table=True):
    __tablename__ = "skills"

    id: str = Field(primary_key=True)
    schema_version: str
    namespace: str = Field(index=True)
    agent_id: str = Field(index=True)
    name: str = Field(index=True)
    description: str
    code: str
    signature: str
    derived_from_json: str
    success_count: int = 0
    failure_count: int = 0
    digest_version: int = Field(index=True)
    created_at: datetime
    superseded_by: str | None = Field(default=None, index=True)
    superseded_at_version: int | None = Field(default=None, index=True)


class RelationshipRow(SQLModel, table=True):
    __tablename__ = "relationships"

    id: str = Field(primary_key=True)
    edge_type: str = Field(index=True)
    from_id: str = Field(index=True)
    from_type: str
    to_id: str = Field(index=True)
    to_type: str
    agent_id: str = Field(index=True)
    namespace: str = Field(index=True)
    confidence: float
    reason: str
    status: str = Field(index=True, default="open")
    created_at: datetime
    resolved_at: datetime | None = Field(default=None)
    resolution_note: str | None = Field(default=None)


# ------------------------------------------------------------ conversion ----
def _utc(dt: datetime | None) -> datetime | None:
    """SQLite loses tzinfo; Mnema stores UTC only, so re-attach it on read."""
    if dt is None:
        return None

    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _to_row(a: Assertion) -> AssertionRow:
    return AssertionRow(
        id=a.id,
        schema_version=a.schema_version,
        namespace=a.namespace,
        agent_id=a.agent_id,
        statement=a.statement,
        subject=a.subject,
        predicate=a.predicate,
        object=a.object,
        valid_from=a.valid_from,
        valid_to=a.valid_to,
        asserted_at=a.asserted_at,
        provenance_json=a.provenance.model_dump_json(),
        confidence_json=a.confidence.model_dump_json(),
        status=a.status.value,
        supersedes=a.supersedes,
        content_hash=a.content_hash,
    )


def _from_row(r: AssertionRow) -> Assertion:
    return Assertion.model_validate(
        {
            "id": r.id,
            "schema_version": r.schema_version,
            "namespace": r.namespace,
            "agent_id": r.agent_id,
            "statement": r.statement,
            "subject": r.subject,
            "predicate": r.predicate,
            "object": r.object,
            "valid_from": _utc(r.valid_from),
            "valid_to": _utc(r.valid_to),
            "asserted_at": _utc(r.asserted_at),
            "provenance": json.loads(r.provenance_json),
            "confidence": json.loads(r.confidence_json),
            "status": r.status,
            "supersedes": r.supersedes,
        }
    )


def _rule_to_row(r: Rule, *, namespace: str, agent_id: str) -> RuleRow:
    return RuleRow(
        id=r.id,
        schema_version=r.schema_version,
        namespace=namespace,
        agent_id=agent_id,
        text=r.text,
        scope=r.scope,
        derived_from_json=json.dumps(list(r.derived_from)),
        confidence=r.confidence,
        digest_version=r.digest_version,
        created_at=r.created_at,
        superseded_by=r.superseded_by,
    )


def _rule_from_row(r: RuleRow) -> Rule:
    return Rule.model_validate(
        {
            "id": r.id,
            "schema_version": r.schema_version,
            "text": r.text,
            "scope": r.scope,
            "derived_from": tuple(json.loads(r.derived_from_json)),
            "confidence": r.confidence,
            "digest_version": r.digest_version,
            "created_at": _utc(r.created_at),
            "superseded_by": r.superseded_by,
        }
    )


def _skill_to_row(s: Skill, *, namespace: str, agent_id: str) -> SkillRow:
    return SkillRow(
        id=s.id,
        schema_version=s.schema_version,
        namespace=namespace,
        agent_id=agent_id,
        name=s.name,
        description=s.description,
        code=s.code,
        signature=s.signature,
        derived_from_json=json.dumps(list(s.derived_from)),
        success_count=s.success_count,
        failure_count=s.failure_count,
        digest_version=s.digest_version,
        created_at=s.created_at,
        superseded_by=s.superseded_by,
    )


def _skill_from_row(r: SkillRow) -> Skill:
    return Skill.model_validate(
        {
            "id": r.id,
            "schema_version": r.schema_version,
            "name": r.name,
            "description": r.description,
            "code": r.code,
            "signature": r.signature,
            "derived_from": tuple(json.loads(r.derived_from_json)),
            "success_count": r.success_count,
            "failure_count": r.failure_count,
            "digest_version": r.digest_version,
            "created_at": _utc(r.created_at),
            "superseded_by": r.superseded_by,
        }
    )


# ------------------------------------------------------------- repository ---
class SqliteMnemaStore:
    """Implements both EventLog and AssertionRepository against one SQLite file."""

    def __init__(self, url: str = "sqlite:///mnema.db") -> None:
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    # -- EventLog -------------------------------------------------------------
    def append(self, event: Event) -> int:
        with Session(self.engine) as s:
            seq = self._append_in(s, event)
            s.commit()
            return seq

    @staticmethod
    def _append_in(s: Session, event: Event) -> int:
        row = EventRow(
            event_id=event.event_id,
            event_type=event.event_type.value,
            occurred_at=event.occurred_at,
            namespace=event.namespace,
            agent_id=event.agent_id,
            assertion_id=event.assertion_id,
            payload_json=json.dumps(event.payload, default=str),
        )
        s.add(row)
        s.flush()
        if row.seq is None:
            raise RuntimeError(
                f"WAL flush produced no seq for event {event.event_id!r}. "
                "This indicates a database integrity failure."
            )
        return row.seq

    def read(self, *, after_seq: int = 0, limit: int | None = None) -> Iterable[tuple[int, Event]]:
        with Session(self.engine) as s:
            q = select(EventRow).where(EventRow.seq > after_seq).order_by(EventRow.seq)
            if limit is not None:
                q = q.limit(limit)
            rows = s.exec(q).all()
        for r in rows:
            yield (
                r.seq or 0,
                Event(
                    event_id=r.event_id,
                    event_type=EventType(r.event_type),
                    occurred_at=_utc(r.occurred_at),
                    namespace=r.namespace,
                    agent_id=r.agent_id,
                    assertion_id=r.assertion_id,
                    payload=json.loads(r.payload_json),
                ),
            )

    # -- AssertionRepository ----------------------------------------------------
    def record(self, assertion: Assertion) -> None:
        with Session(self.engine) as s:  # ONE transaction: event + projection
            self._append_in(
                s,
                Event(
                    event_type=EventType.ASSERTION_RECORDED,
                    namespace=assertion.namespace,
                    agent_id=assertion.agent_id,
                    assertion_id=assertion.id,
                    payload={"content_hash": assertion.content_hash},
                ),
            )
            s.add(_to_row(assertion))
            if assertion.supersedes:
                prev = s.get(AssertionRow, assertion.supersedes)
                if prev is not None:
                    prev.superseded_by = assertion.id
                    s.add(prev)
                    self._append_in(
                        s,
                        Event(
                            event_type=EventType.ASSERTION_SUPERSEDED,
                            namespace=assertion.namespace,
                            agent_id=assertion.agent_id,
                            assertion_id=assertion.supersedes,
                            payload={"superseded_by": assertion.id},
                        ),
                    )
            s.commit()

    def get(self, assertion_id: str) -> Assertion | None:
        with Session(self.engine) as s:
            row = s.get(AssertionRow, assertion_id)
            return _from_row(row) if row else None

    def current_beliefs(
        self,
        *,
        namespace: str = "default",
        agent_id: str | None = None,
        subject: str | None = None,
        as_of: datetime | None = None,
    ) -> list[Assertion]:
        with Session(self.engine) as s:
            q = select(AssertionRow).where(
                AssertionRow.namespace == namespace,
                AssertionRow.status.not_in(["retracted", "deprecated"]),  # type: ignore[attr-defined]
            )
            if agent_id:
                q = q.where(AssertionRow.agent_id == agent_id)
            if subject:
                q = q.where(AssertionRow.subject == subject)
            if as_of:
                # time-travel: only what was asserted by then...
                q = q.where(AssertionRow.asserted_at <= as_of)
            rows = s.exec(q).all()

        if as_of:
            # ...and not yet superseded by anything asserted by then.
            by_then = {r.id for r in rows}
            rows = [r for r in rows if not (r.superseded_by and r.superseded_by in by_then)]
        else:
            rows = [r for r in rows if r.superseded_by is None]
        return [_from_row(r) for r in rows]

    # -- ConsolidationRepository -----------------------------------------------
    def save_digest(self, digest: Digest) -> None:
        """Atomic: CONSOLIDATION_RUN event + digest row + rule/skill rows +
        supersession links on the previous version's matching rows.

        Supersession policy (adapter-managed, per ADR-0002):
          - A new rule supersedes any active rule with the same
            (namespace, agent_id, scope).
          - A new skill supersedes any active skill with the same
            (namespace, agent_id, name).
        The old row's `superseded_by` and `superseded_at_version` are set in
        the same transaction. Old rows are preserved for `get_digest(v)`
        time-travel.
        """
        with Session(self.engine) as s:
            # 1. Emit CONSOLIDATION_RUN event (WAL is the source of truth).
            self._append_in(
                s,
                Event(
                    event_type=EventType.CONSOLIDATION_RUN,
                    namespace=digest.namespace,
                    agent_id=digest.agent_id,
                    payload={
                        "version": digest.version,
                        "source_seq_range": list(digest.source_event_seq_range),
                        "rule_ids": list(digest.rule_ids),
                        "skill_ids": list(digest.skill_ids),
                    },
                ),
            )
            # 2. Manifest row.
            s.add(
                DigestRow(
                    schema_version=digest.schema_version,
                    version=digest.version,
                    namespace=digest.namespace,
                    agent_id=digest.agent_id,
                    source_seq_start=digest.source_event_seq_range[0],
                    source_seq_end=digest.source_event_seq_range[1],
                    created_at=digest.created_at,
                )
            )
            # 3. Rule rows + scope-based supersession of prior actives.
            for rule in digest.rules:
                prior_rules = s.exec(
                    select(RuleRow).where(
                        RuleRow.namespace == digest.namespace,
                        RuleRow.agent_id == digest.agent_id,
                        RuleRow.scope == rule.scope,
                        RuleRow.superseded_by.is_(None),  # type: ignore[union-attr]
                    )
                ).all()
                for prev in prior_rules:
                    prev.superseded_by = rule.id
                    prev.superseded_at_version = digest.version
                    s.add(prev)
                s.add(_rule_to_row(rule, namespace=digest.namespace, agent_id=digest.agent_id))
            # 4. Skill rows + name-based supersession of prior actives.
            for skill in digest.skills:
                prior_skills = s.exec(
                    select(SkillRow).where(
                        SkillRow.namespace == digest.namespace,
                        SkillRow.agent_id == digest.agent_id,
                        SkillRow.name == skill.name,
                        SkillRow.superseded_by.is_(None),  # type: ignore[union-attr]
                    )
                ).all()
                for prev in prior_skills:
                    prev.superseded_by = skill.id
                    prev.superseded_at_version = digest.version
                    s.add(prev)
                s.add(_skill_to_row(skill, namespace=digest.namespace, agent_id=digest.agent_id))
            s.commit()

    def latest_digest(
        self, *, namespace: str = "default", agent_id: str | None = None
    ) -> Digest | None:
        with Session(self.engine) as s:
            q = select(DigestRow).where(DigestRow.namespace == namespace)
            if agent_id is not None:
                q = q.where(DigestRow.agent_id == agent_id)
            q = q.order_by(DigestRow.version.desc()).limit(1)  # type: ignore[union-attr]
            row = s.exec(q).first()
        if row is None:
            return None
        return self.get_digest(row.version, namespace=namespace, agent_id=row.agent_id)

    def get_digest(
        self, version: int, *, namespace: str = "default", agent_id: str | None = None
    ) -> Digest | None:
        with Session(self.engine) as s:
            q = select(DigestRow).where(
                DigestRow.namespace == namespace, DigestRow.version == version
            )
            if agent_id is not None:
                q = q.where(DigestRow.agent_id == agent_id)
            drow = s.exec(q).first()
            if drow is None:
                return None
            resolved_agent = drow.agent_id

            # Active-as-of `version`: created at or before `version`, not
            # superseded at or before `version`.
            rrows = s.exec(
                select(RuleRow).where(
                    RuleRow.namespace == namespace,
                    RuleRow.agent_id == resolved_agent,
                    RuleRow.digest_version <= version,
                )
            ).all()
            active_rules = [
                _rule_from_row(r)
                for r in rrows
                if r.superseded_at_version is None or r.superseded_at_version > version
            ]

            srows = s.exec(
                select(SkillRow).where(
                    SkillRow.namespace == namespace,
                    SkillRow.agent_id == resolved_agent,
                    SkillRow.digest_version <= version,
                )
            ).all()
            active_skills = [
                _skill_from_row(r)
                for r in srows
                if r.superseded_at_version is None or r.superseded_at_version > version
            ]

        return Digest(
            schema_version=drow.schema_version,
            version=drow.version,
            namespace=drow.namespace,
            agent_id=resolved_agent,
            rules=tuple(active_rules),
            skills=tuple(active_skills),
            source_event_seq_range=(drow.source_seq_start, drow.source_seq_end),
            created_at=_utc(drow.created_at),  # type: ignore[arg-type]
        )

    def active_rules(
        self,
        *,
        namespace: str = "default",
        agent_id: str | None = None,
        as_of_version: int | None = None,
    ) -> list[Rule]:
        with Session(self.engine) as s:
            q = select(RuleRow).where(RuleRow.namespace == namespace)
            if agent_id is not None:
                q = q.where(RuleRow.agent_id == agent_id)
            if as_of_version is not None:
                q = q.where(RuleRow.digest_version <= as_of_version)
            rows = s.exec(q).all()
        if as_of_version is None:
            rows = [r for r in rows if r.superseded_by is None]
        else:
            rows = [
                r
                for r in rows
                if r.superseded_at_version is None or r.superseded_at_version > as_of_version
            ]
        return [_rule_from_row(r) for r in rows]

    def active_skills(
        self,
        *,
        namespace: str = "default",
        agent_id: str | None = None,
        as_of_version: int | None = None,
    ) -> list[Skill]:
        with Session(self.engine) as s:
            q = select(SkillRow).where(SkillRow.namespace == namespace)
            if agent_id is not None:
                q = q.where(SkillRow.agent_id == agent_id)
            if as_of_version is not None:
                q = q.where(SkillRow.digest_version <= as_of_version)
            rows = s.exec(q).all()
        if as_of_version is None:
            rows = [r for r in rows if r.superseded_by is None]
        else:
            rows = [
                r
                for r in rows
                if r.superseded_at_version is None or r.superseded_at_version > as_of_version
            ]
        return [_skill_from_row(r) for r in rows]

    def mark_skill_outcome(self, skill_id: str, *, success: bool) -> None:
        """SQL-level atomic increment — no read-modify-write race (ADR-0002)."""
        with Session(self.engine) as s:
            col = SkillRow.success_count if success else SkillRow.failure_count
            result = s.exec(  # type: ignore[call-overload]
                update(SkillRow)
                .where(SkillRow.id == skill_id)
                .values({col.key: col + 1})
            )
            if result.rowcount == 0:
                raise KeyError(f"skill not found: {skill_id!r}")
            s.commit()

    # -- Graph layer -----------------------------------------------------------

    def assert_relationship(self, rel: Relationship) -> None:
        """Persist a relationship AND emit RELATIONSHIP_ASSERTED to the WAL atomically."""
        from graxella.mnema.core.events import Event, EventType
        row = RelationshipRow(
            id=rel.id,
            edge_type=rel.edge_type.value,
            from_id=rel.from_id,
            from_type=rel.from_type.value,
            to_id=rel.to_id,
            to_type=rel.to_type.value,
            agent_id=rel.agent_id,
            namespace=rel.namespace,
            confidence=rel.confidence,
            reason=rel.reason,
            status=rel.status.value,
            created_at=rel.created_at,
        )
        event = Event(
            event_type=EventType.RELATIONSHIP_ASSERTED,
            namespace=rel.namespace,
            agent_id=rel.agent_id,
            payload={
                "relationship_id": rel.id,
                "edge_type": rel.edge_type.value,
                "from_id": rel.from_id,
                "to_id": rel.to_id,
                "reason": rel.reason,
                "confidence": rel.confidence,
            },
        )
        with Session(self.engine) as s:
            s.add(row)
            self._append_in(s, event)
            s.commit()

    def resolve_relationship(self, rel_id: str, *, note: str, auto: bool = False) -> Relationship:
        """Mark a relationship resolved and emit RELATIONSHIP_RESOLVED to the WAL."""
        from datetime import UTC, datetime

        from graxella.mnema.core.events import Event, EventType

        with Session(self.engine) as s:
            row = s.get(RelationshipRow, rel_id)
            if row is None:
                raise KeyError(f"relationship not found: {rel_id!r}")
            new_status = RelationshipStatus.AUTO_RESOLVED if auto else RelationshipStatus.RESOLVED
            resolved_at = datetime.now(UTC)
            s.exec(  # type: ignore[call-overload]
                update(RelationshipRow)
                .where(RelationshipRow.id == rel_id)
                .values(
                    status=new_status.value,
                    resolved_at=resolved_at,
                    resolution_note=note,
                )
            )
            event = Event(
                event_type=EventType.RELATIONSHIP_RESOLVED,
                namespace=row.namespace,
                agent_id=row.agent_id,
                payload={
                    "relationship_id": rel_id,
                    "resolution": note,
                    "auto": auto,
                },
            )
            self._append_in(s, event)
            s.commit()

        # Return the updated Relationship domain object
        rel = self.get_relationship(rel_id)
        assert rel is not None
        return rel

    def get_relationship(self, rel_id: str) -> Relationship | None:
        with Session(self.engine) as s:
            row = s.get(RelationshipRow, rel_id)
            return _rel_from_row(row) if row else None

    def open_relationships(
        self,
        agent_id: str,
        *,
        namespace: str,
        edge_type: str | None = None,
    ) -> list[Relationship]:
        """Return all unresolved relationships for an agent."""
        with Session(self.engine) as s:
            q = select(RelationshipRow).where(
                RelationshipRow.agent_id == agent_id,
                RelationshipRow.namespace == namespace,
                RelationshipRow.status == RelationshipStatus.OPEN.value,
            )
            if edge_type:
                q = q.where(RelationshipRow.edge_type == edge_type)
            rows = s.exec(q).all()
            return [_rel_from_row(r) for r in rows]

    def relationships_involving(self, assertion_id: str) -> list[Relationship]:
        """Return all relationships where assertion_id appears as from or to."""
        with Session(self.engine) as s:
            rows = s.exec(
                select(RelationshipRow).where(
                    (RelationshipRow.from_id == assertion_id)
                    | (RelationshipRow.to_id == assertion_id)
                )
            ).all()
            return [_rel_from_row(r) for r in rows]


def _rel_from_row(r: RelationshipRow) -> Relationship:
    return Relationship.model_validate(
        {
            "id": r.id,
            "edge_type": r.edge_type,
            "from_id": r.from_id,
            "from_type": r.from_type,
            "to_id": r.to_id,
            "to_type": r.to_type,
            "agent_id": r.agent_id,
            "namespace": r.namespace,
            "confidence": r.confidence,
            "reason": r.reason,
            "status": r.status,
            "created_at": _utc(r.created_at),
            "resolved_at": _utc(r.resolved_at),
            "resolution_note": r.resolution_note,
        }
    )
