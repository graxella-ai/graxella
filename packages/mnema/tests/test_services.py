"""Tests for services layer: MemoryRecorder, SleepConsolidator, MemoryRetriever, AuditService."""

from __future__ import annotations

import pytest

from mnema.adapters.llm.fake import FakeLLM
from mnema.adapters.sqlite.repository import SqliteMnemaStore
from mnema.core.events import EventType
from mnema.services.auditor import AuditService
from mnema.services.consolidator import SleepConsolidator
from mnema.services.recorder import MemoryRecorder
from mnema.services.retriever import MemoryRetriever


def _store() -> SqliteMnemaStore:
    return SqliteMnemaStore("sqlite:///:memory:")


# ── MemoryRecorder ────────────────────────────────────────────────────────────

def test_observe_creates_assertion_and_wal_event() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    a = rec.observe("agent-1", "weatherlib.get_weather() removed", subject="weatherlib")

    assert store.get(a.id) == a
    events = list(store.read())
    assert len(events) == 1
    assert events[0][1].event_type is EventType.ASSERTION_RECORDED
    assert events[0][1].assertion_id == a.id


def test_observe_default_confidence() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    a = rec.observe("agent-1", "some fact")
    assert a.confidence.value == 0.85


def test_revise_creates_supersession_link() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    original = rec.observe("agent-1", "rate limit is 100 rpm", subject="api")
    revised = rec.revise(original.id, statement="rate limit is 60 rpm", confidence=0.99)

    assert revised.supersedes == original.id
    assert revised.confidence.value == 0.99
    current = store.current_beliefs(subject="api", agent_id="agent-1")
    assert [b.id for b in current] == [revised.id]


def test_revise_missing_id_raises_key_error() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    with pytest.raises(KeyError):
        rec.revise("asr_missing", statement="x")


def test_retract_sets_retracted_status() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    a = rec.observe("agent-1", "old fact", subject="s")
    rec.retract(a.id)
    assert store.current_beliefs(subject="s", agent_id="agent-1") == []


def test_retract_missing_id_raises_key_error() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    with pytest.raises(KeyError):
        rec.retract("asr_missing")


def test_retraction_cascade_returns_at_risk_rule_ids() -> None:
    from mnema.core.consolidation import Digest, Rule

    store = _store()
    rec = MemoryRecorder(store)
    a = rec.observe("agent-1", "use fetch_v2()", subject="lib")

    rule = Rule(
        text="use fetch_v2()",
        scope="lib",
        derived_from=(a.id,),
        confidence=0.9,
        digest_version=1,
    )
    digest = Digest(
        version=1, agent_id="agent-1",
        rules=(rule,), skills=(),
        source_event_seq_range=(0, 1),
    )
    store.save_digest(digest)

    at_risk = rec.retraction_cascade(a.id)
    assert rule.id in at_risk


def test_retraction_cascade_unknown_id_returns_empty() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    assert rec.retraction_cascade("asr_unknown") == []


# ── SleepConsolidator ─────────────────────────────────────────────────────────

def test_consolidate_produces_digest_and_consolidation_run_event() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "get_weather() removed", subject="weatherlib")
    rec.observe("agent-1", "use fetch_forecast(city)", subject="weatherlib")

    llm = FakeLLM.from_scenario("weatherlib_v2")
    consolidator = SleepConsolidator(store, llm)
    digest = consolidator.consolidate("agent-1")

    assert digest is not None
    assert digest.version == 1
    assert len(digest.rules) == 1
    assert "fetch_forecast" in digest.rules[0].text
    assert len(digest.skills) == 1

    events = [e for _, e in store.read() if e.event_type is EventType.CONSOLIDATION_RUN]
    assert len(events) == 1
    assert events[0].payload["version"] == 1


def test_consolidate_returns_none_with_fewer_than_two_beliefs() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "only one fact")

    llm = FakeLLM()
    consolidator = SleepConsolidator(store, llm)
    assert consolidator.consolidate("agent-1") is None


def test_consolidate_increments_digest_version() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "fact a", subject="lib")
    rec.observe("agent-1", "fact b", subject="lib")

    llm = FakeLLM.from_scenario("weatherlib_v2")
    consolidator = SleepConsolidator(store, llm)
    d1 = consolidator.consolidate("agent-1")
    assert d1 is not None and d1.version == 1

    rec.observe("agent-1", "fact c", subject="lib")
    rec.observe("agent-1", "fact d", subject="lib")
    d2 = consolidator.consolidate("agent-1")
    assert d2 is not None and d2.version == 2


def test_consolidate_skips_invalid_skill_proposals() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "fact a")
    rec.observe("agent-1", "fact b")

    bad_skills = [{"name": "broken", "description": "d",
                   "code": "def broken(:\n  pass\n",  # syntax error
                   "signature": "def broken()", "derived_from": ("x",)}]
    llm = FakeLLM(skills=bad_skills)
    consolidator = SleepConsolidator(store, llm)
    digest = consolidator.consolidate("agent-1")
    assert digest is not None
    assert len(digest.skills) == 0  # bad skill was skipped


# ── MemoryRetriever ───────────────────────────────────────────────────────────

def test_retriever_render_empty_when_no_digest() -> None:
    store = _store()
    ret = MemoryRetriever(store)
    assert ret.render("agent-1") == ""


def test_retriever_render_returns_markdown_after_consolidation() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "fact a", subject="lib")
    rec.observe("agent-1", "fact b", subject="lib")

    llm = FakeLLM.from_scenario("weatherlib_v2")
    SleepConsolidator(store, llm).consolidate("agent-1")

    rendered = MemoryRetriever(store).render("agent-1")
    assert rendered.startswith("# Consolidated memory")
    assert "## Rules" in rendered


def test_retriever_belief_count() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "f1")
    rec.observe("agent-1", "f2")
    assert MemoryRetriever(store).belief_count("agent-1") == 2


def test_retriever_latest_digest_version_none_before_consolidation() -> None:
    store = _store()
    assert MemoryRetriever(store).latest_digest_version("agent-1") is None


def test_retriever_search_by_subject() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "about lib", subject="mylib")
    rec.observe("agent-1", "about other", subject="other")
    results = MemoryRetriever(store).search("agent-1", "mylib")
    assert len(results) == 1
    assert results[0].subject == "mylib"


# ── AuditService ─────────────────────────────────────────────────────────────

def test_auditor_why_believed_returns_structure() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    a = rec.observe("agent-1", "the API changed", subject="api")

    result = AuditService(store).why_believed(a.id)
    assert result["assertion_id"] == a.id
    assert result["assertion"]["id"] == a.id
    assert isinstance(result["wal_events"], list)
    assert len(result["wal_events"]) == 1
    assert result["wal_events"][0]["type"] == "assertion.recorded"


def test_auditor_why_believed_not_found() -> None:
    store = _store()
    result = AuditService(store).why_believed("asr_missing")
    assert "error" in result


def test_auditor_belief_timeline_ordered_by_seq() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    a1 = rec.observe("agent-1", "rate limit 100 rpm", subject="api")
    rec.revise(a1.id, statement="rate limit 60 rpm")

    timeline = AuditService(store).belief_timeline("agent-1", "api")
    seqs = [e["seq"] for e in timeline]
    assert seqs == sorted(seqs)
    assert len(timeline) >= 2


def test_auditor_digest_diff_identifies_added_and_removed_rules() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    for i in range(4):
        rec.observe("agent-1", f"fact {i}", subject="lib")

    llm_v2 = FakeLLM.from_scenario("weatherlib_v2")
    SleepConsolidator(store, llm_v2).consolidate("agent-1")

    for i in range(2):
        rec.observe("agent-1", f"v3 fact {i}", subject="lib")

    llm_v3 = FakeLLM.from_scenario("weatherlib_v3")
    SleepConsolidator(store, llm_v3).consolidate("agent-1")

    diff = AuditService(store).digest_diff("agent-1", 1, 2)
    assert diff["from_version"] == 1
    assert diff["to_version"] == 2
    assert isinstance(diff["rules"]["added"], list)
    assert isinstance(diff["rules"]["scope_updated"], list)


def test_auditor_digest_diff_missing_version_returns_error() -> None:
    store = _store()
    result = AuditService(store).digest_diff("agent-1", 1, 99)
    assert "error" in result


def test_auditor_compliance_report_structure() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "fact a", subject="lib")
    rec.observe("agent-1", "fact b", subject="lib")

    report = AuditService(store).compliance_report("agent-1")
    assert report["agent_id"] == "agent-1"
    assert "total_wal_events" in report
    assert "active_rules" in report
    assert "supersession_chains" in report
