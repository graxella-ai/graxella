"""SQLite adapter tests: persistence roundtrip, WAL append, supersession, time travel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mnema.adapters.sqlite.repository import SqliteMnemaStore
from mnema.core.events import EventType
from mnema.core.models import Assertion, Confidence, OriginType, Provenance


def _mk(statement: str, subject: str | None = None, agent: str = "agent-1") -> Assertion:
    return Assertion(
        agent_id=agent,
        statement=statement,
        subject=subject,
        provenance=Provenance(origin_type=OriginType.OBSERVED, source_id="test"),
        confidence=Confidence(value=0.9, method="test"),
    )


def test_record_and_get_roundtrip(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    a = _mk("Neo4j 5.x deprecates apoc.create.uuid", subject="neo4j")
    store.record(a)
    got = store.get(a.id)
    assert got == a  # lossless through the DB


def test_record_appends_wal_event(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    a = _mk("fact one")
    store.record(a)
    events = list(store.read())
    assert len(events) == 1
    seq, evt = events[0]
    assert seq == 1
    assert evt.event_type is EventType.ASSERTION_RECORDED
    assert evt.assertion_id == a.id
    assert evt.payload["content_hash"] == a.content_hash


def test_supersession_hides_old_belief_and_logs_event(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    v1 = _mk("The API rate limit is 100 rpm", subject="api")
    store.record(v1)
    v2 = v1.revise(statement="The API rate limit is 60 rpm")
    store.record(v2)

    current = store.current_beliefs(subject="api")
    assert [a.id for a in current] == [v2.id]  # v1 no longer current

    types = [e.event_type for _, e in store.read()]
    assert types == [
        EventType.ASSERTION_RECORDED,
        EventType.ASSERTION_RECORDED,
        EventType.ASSERTION_SUPERSEDED,
    ]


def test_time_travel_as_of_returns_past_belief(tmp_path) -> None:
    """The differential-evaluation primitive: what did the agent believe THEN?"""
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    t0 = datetime.now(UTC)

    v1 = _mk("Sridhar owns the Python pipeline", subject="ownership")
    store.record(v1)
    checkpoint = t0 + timedelta(seconds=1)

    v2 = Assertion(
        agent_id=v1.agent_id,
        statement="Sridhar owns pipeline AND inference",
        subject="ownership",
        provenance=v1.provenance,
        confidence=v1.confidence,
        supersedes=v1.id,
        asserted_at=t0 + timedelta(seconds=5),
    )
    store.record(v2)

    then = store.current_beliefs(subject="ownership", as_of=checkpoint)
    now = store.current_beliefs(subject="ownership")
    assert [a.id for a in then] == [v1.id]
    assert [a.id for a in now] == [v2.id]


def test_retraction_removes_from_current(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    a = _mk("wrong fact", subject="s")
    store.record(a)
    store.record(a.retract())
    assert store.current_beliefs(subject="s") == []
