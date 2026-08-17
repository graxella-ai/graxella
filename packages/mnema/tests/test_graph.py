"""Tests for the typed temporal graph layer (KnowledgeGraph + contradiction workflow)."""

from __future__ import annotations

import pytest

from mnema.adapters.sqlite.repository import SqliteMnemaStore
from mnema.core.graph import RelationshipStatus
from mnema.services.graph import KnowledgeGraph
from mnema.services.recorder import MemoryRecorder


@pytest.fixture()
def store():
    return SqliteMnemaStore("sqlite:///:memory:")


@pytest.fixture()
def recorder(store):
    return MemoryRecorder(store)


@pytest.fixture()
def kg(store):
    return KnowledgeGraph(store)


# ── assert_contradicts ────────────────────────────────────────────────────────

def test_assert_contradicts_creates_open_relationship(store, recorder, kg):
    a1 = recorder.observe("agent", "Rate limit is 100 rpm", subject="api")
    a2 = recorder.observe("agent", "Rate limit is 60 rpm", subject="api")

    rel = kg.assert_contradicts(
        "agent", a1.id, a2.id, reason="Two conflicting rate limit values."
    )

    assert rel.status == RelationshipStatus.OPEN
    assert rel.from_id == a1.id
    assert rel.to_id == a2.id
    assert "rate limit" in rel.reason.lower()


def test_assert_contradicts_emits_wal_event(store, recorder, kg):
    a1 = recorder.observe("agent", "endpoint is /v1/data")
    a2 = recorder.observe("agent", "endpoint is /v2/data")

    kg.assert_contradicts("agent", a1.id, a2.id, reason="Conflicting endpoint versions.")

    events = [e for _, e in store.read()]
    event_types = [e.event_type for e in events]
    assert "relationship.asserted" in event_types


def test_assert_contradicts_raises_on_missing_assertion(store, kg):
    with pytest.raises(KeyError):
        kg.assert_contradicts("agent", "nonexistent-id", "also-nonexistent", reason="x")


def test_open_contradictions_returns_enriched_dicts(store, recorder, kg):
    a1 = recorder.observe("agent", "Use API key auth", subject="auth")
    a2 = recorder.observe("agent", "No authentication required", subject="auth")

    kg.assert_contradicts("agent", a1.id, a2.id, reason="Conflicting auth requirements.")

    items = kg.open_contradictions("agent")
    assert len(items) == 1
    item = items[0]
    assert item["from_id"] == a1.id
    assert item["to_id"] == a2.id
    assert "Use API key auth" in item["from_statement"]
    assert "No authentication required" in item["to_statement"]


# ── resolve ───────────────────────────────────────────────────────────────────

def test_resolve_marks_relationship_resolved(store, recorder, kg):
    a1 = recorder.observe("agent", "Use v1 endpoint")
    a2 = recorder.observe("agent", "Use v2 endpoint")
    rel = kg.assert_contradicts("agent", a1.id, a2.id, reason="Version conflict.")

    resolved = kg.resolve(rel.id, note="v2 endpoint confirmed correct per changelog.")

    assert resolved.status == RelationshipStatus.RESOLVED
    assert resolved.resolution_note is not None
    assert "v2" in resolved.resolution_note
    assert resolved.resolved_at is not None


def test_resolve_emits_wal_event(store, recorder, kg):
    a1 = recorder.observe("agent", "timeout is 30s")
    a2 = recorder.observe("agent", "timeout is 60s")
    rel = kg.assert_contradicts("agent", a1.id, a2.id, reason="Conflicting timeouts.")

    kg.resolve(rel.id, note="60s confirmed in docs.")

    events = [e for _, e in store.read()]
    event_types = [e.event_type for e in events]
    assert "relationship.resolved" in event_types


def test_resolved_contradiction_no_longer_open(store, recorder, kg):
    a1 = recorder.observe("agent", "cache TTL is 5min")
    a2 = recorder.observe("agent", "cache TTL is 10min")
    rel = kg.assert_contradicts("agent", a1.id, a2.id, reason="TTL conflict.")

    kg.resolve(rel.id, note="10min confirmed.")

    open_items = kg.open_contradictions("agent")
    assert len(open_items) == 0


# ── auto-resolution on retraction ─────────────────────────────────────────────

def test_retraction_auto_resolves_contradiction(store, recorder, kg):
    a1 = recorder.observe("agent", "max retries is 3")
    a2 = recorder.observe("agent", "max retries is 5")
    rel = kg.assert_contradicts("agent", a1.id, a2.id, reason="Conflicting retry limits.")

    # Retract one side — contradiction should auto-resolve
    recorder.retract(a1.id)

    updated = store.get_relationship(rel.id)
    assert updated is not None
    assert updated.status == RelationshipStatus.AUTO_RESOLVED
    assert updated.resolution_note is not None


def test_retraction_auto_resolved_not_in_open_list(store, recorder, kg):
    a1 = recorder.observe("agent", "log level is DEBUG")
    a2 = recorder.observe("agent", "log level is INFO")
    kg.assert_contradicts("agent", a1.id, a2.id, reason="Conflicting log levels.")

    recorder.retract(a2.id)

    assert len(kg.open_contradictions("agent")) == 0


# ── assert_from_proposals (LLM batch) ────────────────────────────────────────

def test_assert_from_proposals_creates_relationships(store, recorder, kg):
    from mnema.core.graph import ContradictionProposal

    a1 = recorder.observe("agent", "Rate limit is 100 rpm")
    a2 = recorder.observe("agent", "Rate limit is 60 rpm")

    proposals = [
        ContradictionProposal(
            from_id=a1.id,
            to_id=a2.id,
            reason="Conflicting rate limit values.",
            confidence=0.9,
        )
    ]
    created = kg.assert_from_proposals("agent", proposals)
    assert len(created) == 1
    assert created[0].status == RelationshipStatus.OPEN


def test_assert_from_proposals_skips_missing_assertions(store, recorder, kg):
    from mnema.core.graph import ContradictionProposal

    recorder.observe("agent", "only one assertion")

    proposals = [
        ContradictionProposal(
            from_id="ghost-id-1",
            to_id="ghost-id-2",
            reason="Neither assertion exists.",
            confidence=0.9,
        )
    ]
    created = kg.assert_from_proposals("agent", proposals)
    assert len(created) == 0


# ── consolidator integration ──────────────────────────────────────────────────

def test_consolidator_detects_contradictions(store):
    from mnema.adapters.llm.fake import FakeLLM
    from mnema.services.consolidator import SleepConsolidator

    rec = MemoryRecorder(store)
    AGENT = "contradict-agent"
    a1 = rec.observe(AGENT, "Rate limit is 100 rpm", subject="api")
    a2 = rec.observe(AGENT, "Rate limit is 60 rpm", subject="api")

    llm = FakeLLM.from_scenario("contradicts_rate_limit")
    SleepConsolidator(store, llm).consolidate(AGENT)

    kg = KnowledgeGraph(store)
    open_c = kg.open_contradictions(AGENT)
    assert len(open_c) >= 1
    assert any(
        c["from_id"] in (a1.id, a2.id) and c["to_id"] in (a1.id, a2.id)
        for c in open_c
    )


# ── contradiction_summary ─────────────────────────────────────────────────────

def test_contradiction_summary_clean(store, recorder, kg):
    recorder.observe("agent", "single clean assertion")
    summary = kg.contradiction_summary("agent")
    assert summary["knowledge_integrity"] == "clean"
    assert summary["open_contradictions"] == 0


def test_contradiction_summary_conflicts_present(store, recorder, kg):
    a1 = recorder.observe("agent", "X is true")
    a2 = recorder.observe("agent", "X is false")
    kg.assert_contradicts("agent", a1.id, a2.id, reason="Direct contradiction.")

    summary = kg.contradiction_summary("agent")
    assert summary["knowledge_integrity"] == "conflicts_present"
    assert summary["open_contradictions"] == 1
