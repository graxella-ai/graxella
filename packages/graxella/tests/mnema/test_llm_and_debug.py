"""Tests for FakeLLM, debug layer (inspector, replay, diff), and MnemaClient SDK."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from graxella.mnema.adapters.llm.fake import FakeLLM
from graxella.mnema.adapters.sqlite.repository import SqliteMnemaStore
from graxella.mnema.core.consolidation import Digest, Rule, Skill
from graxella.mnema.debug.diff import diff_digests
from graxella.mnema.debug.inspector import BeliefInspector
from graxella.mnema.debug.replay import EventReplayer
from graxella.mnema.integrations.sdk import MnemaClient
from graxella.mnema.services.consolidator import SleepConsolidator
from graxella.mnema.services.recorder import MemoryRecorder


def _store() -> SqliteMnemaStore:
    return SqliteMnemaStore("sqlite:///:memory:")


def _rule(scope: str, dv: int = 1, text: str = "do x") -> Rule:
    return Rule(text=text, scope=scope, derived_from=("asrt_1",),
                confidence=0.9, digest_version=dv)


def _skill(name: str, dv: int = 1) -> Skill:
    return Skill(name=name, description=f"{name} skill",
                 code=f"def {name}(x): return x\n",
                 signature=f"def {name}(x: int) -> int",
                 derived_from=("asrt_1",), digest_version=dv)


# ── FakeLLM ───────────────────────────────────────────────────────────────────

def test_fake_llm_default_groups_by_subject() -> None:
    llm = FakeLLM()
    dicts = [
        {"id": "a1", "statement": "use fetch_v2()", "subject": "lib", "confidence": 0.9},
        {"id": "a2", "statement": "v2 works fine", "subject": "lib", "confidence": 0.8},
        {"id": "a3", "statement": "other topic", "subject": "other", "confidence": 0.7},
    ]
    rules = llm.extract_rules(dicts)
    # 2 unique subjects -> 2 rules
    assert len(rules) == 2
    scopes = {r.scope for r in rules}
    assert "lib" in scopes and "other" in scopes


def test_fake_llm_default_returns_no_skills() -> None:
    llm = FakeLLM()
    skills = llm.extract_skills([{"id": "a1", "statement": "s", "subject": "x", "confidence": 0.9}])
    assert skills == []


def test_fake_llm_preset_rules_returned_verbatim() -> None:
    preset = [{"text": "use v2", "scope": "lib", "confidence": 0.95, "derived_from": ("a1",)}]
    llm = FakeLLM(rules=preset)
    rules = llm.extract_rules([{"id": "a1", "statement": "s", "subject": "lib", "confidence": 0.9}])
    assert len(rules) == 1
    assert rules[0].text == "use v2"


def test_fake_llm_call_counters() -> None:
    llm = FakeLLM()
    dicts = [{"id": "a1", "statement": "s", "subject": "x", "confidence": 0.8}]
    llm.extract_rules(dicts)
    llm.extract_rules(dicts)
    llm.extract_skills(dicts)
    assert llm.rule_calls == 2
    assert llm.skill_calls == 1


def test_fake_llm_scenario_weatherlib_v2() -> None:
    llm = FakeLLM.from_scenario("weatherlib_v2")
    dicts = [{"id": "a1", "statement": "s", "subject": "weatherlib", "confidence": 0.9},
             {"id": "a2", "statement": "t", "subject": "weatherlib", "confidence": 0.9}]
    rules = llm.extract_rules(dicts)
    assert len(rules) == 1
    assert "fetch_forecast" in rules[0].text
    assert rules[0].scope == "weatherlib"

    skills = llm.extract_skills(dicts)
    assert len(skills) == 1
    assert skills[0].name == "fetch_weather"
    assert "fetch_forecast" in skills[0].code


def test_fake_llm_scenario_empty_returns_nothing() -> None:
    llm = FakeLLM.from_scenario("empty")
    dicts = [{"id": "a1", "statement": "s", "subject": "x", "confidence": 0.9}]
    assert llm.extract_rules(dicts) == []
    assert llm.extract_skills(dicts) == []


def test_fake_llm_respects_max_rules() -> None:
    llm = FakeLLM()
    dicts = [{"id": f"a{i}", "statement": f"fact {i}", "subject": f"s{i}", "confidence": 0.9}
             for i in range(20)]
    rules = llm.extract_rules(dicts, max_rules=3)
    assert len(rules) <= 3


# ── BeliefInspector ───────────────────────────────────────────────────────────

def test_inspector_snapshot_counts() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "fact a", subject="lib")
    rec.observe("agent-1", "fact b", subject="lib")

    snap = BeliefInspector(store).snapshot("agent-1")
    assert snap["agent_id"] == "agent-1"
    assert len(snap["active_beliefs"]) == 2
    assert snap["latest_digest_version"] is None
    assert snap["total_wal_events"] == 2


def test_inspector_snapshot_includes_rules_after_consolidation() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "fact a", subject="lib")
    rec.observe("agent-1", "fact b", subject="lib")
    SleepConsolidator(store, FakeLLM.from_scenario("weatherlib_v2")).consolidate("agent-1")

    snap = BeliefInspector(store).snapshot("agent-1")
    assert len(snap["active_rules"]) == 1
    assert snap["latest_digest_version"] == 1


def test_inspector_trace_returns_wal_events() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    a = rec.observe("agent-1", "some fact", subject="lib")

    trace = BeliefInspector(store).trace(a.id)
    assert trace["found"] is True
    assert len(trace["wal_events"]) == 1
    assert trace["wal_events"][0]["type"] == "assertion.recorded"


def test_inspector_trace_unknown_returns_not_found() -> None:
    store = _store()
    trace = BeliefInspector(store).trace("asr_unknown")
    assert trace["found"] is False
    assert trace["assertion"] is None


def test_inspector_active_at_time_travel() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    t0 = datetime.now(UTC)

    a1 = rec.observe("agent-1", "original fact", subject="s")
    checkpoint = datetime.now(UTC) + timedelta(milliseconds=100)

    # Small sleep substitute: just use an explicit future timestamp for v2
    from graxella.mnema.core.models import Assertion, Confidence, OriginType, Provenance
    a2 = Assertion(
        agent_id="agent-1",
        statement="revised fact",
        subject="s",
        provenance=Provenance(origin_type=OriginType.OBSERVED, source_id="test"),
        confidence=Confidence(value=0.9, method="empirical"),
        supersedes=a1.id,
        asserted_at=t0 + timedelta(seconds=5),
    )
    store.record(a2)

    past = BeliefInspector(store).active_at("agent-1", checkpoint)
    assert any(b["id"] == a1.id for b in past)
    assert not any(b["id"] == a2.id for b in past)


def test_inspector_orphaned_rules_when_source_retracted() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    a = rec.observe("agent-1", "fact a", subject="lib")
    rec.observe("agent-1", "fact b", subject="lib")

    # Save a digest with a rule derived from `a`
    rule = Rule(text="use v2", scope="lib", derived_from=(a.id,),
                confidence=0.9, digest_version=1)
    store.save_digest(Digest(version=1, agent_id="agent-1",
                             rules=(rule,), skills=(),
                             source_event_seq_range=(0, 2)))

    # Retract the source assertion
    rec.retract(a.id)

    orphaned = BeliefInspector(store).orphaned_rules("agent-1")
    assert any(r["id"] == rule.id for r in orphaned)


# ── EventReplayer ─────────────────────────────────────────────────────────────

def test_replayer_events_in_range() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "f1")
    rec.observe("agent-1", "f2")
    rec.observe("agent-1", "f3")

    events = EventReplayer(store).events_in_range(1, 2)
    assert len(events) == 2
    assert all(1 <= e["seq"] <= 2 for e in events)


def test_replayer_consolidation_history() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    rec.observe("agent-1", "f1")
    rec.observe("agent-1", "f2")
    SleepConsolidator(store, FakeLLM.from_scenario("weatherlib_v2")).consolidate("agent-1")

    history = EventReplayer(store).consolidation_history("agent-1")
    assert len(history) == 1
    assert history[0]["version"] == 1


def test_replayer_replay_beliefs_at_seq_reconstructs_state() -> None:
    store = _store()
    rec = MemoryRecorder(store)
    a1 = rec.observe("agent-1", "first belief", subject="s")
    seq_after_first = list(store.read())[-1][0]

    a2 = rec.observe("agent-1", "second belief", subject="s")
    rec.retract(a1.id)

    # At seq_after_first, only a1 should be active
    beliefs_then = EventReplayer(store).replay_beliefs_at_seq("agent-1", seq_after_first)
    assert any(b["id"] == a1.id for b in beliefs_then)
    assert not any(b["id"] == a2.id for b in beliefs_then)


# ── diff_digests ──────────────────────────────────────────────────────────────

def test_diff_digests_identifies_added_and_removed() -> None:
    r1 = _rule("scope_a", 1, "old rule")
    r2 = _rule("scope_b", 2, "new rule")

    d1 = Digest(version=1, agent_id="a1", rules=(r1,), skills=(),
                source_event_seq_range=(0, 5))
    d2 = Digest(version=2, agent_id="a1", rules=(r2,), skills=(),
                source_event_seq_range=(5, 10))

    result = diff_digests(d1, d2)
    assert result["from_version"] == 1
    assert result["to_version"] == 2
    added_ids = [r["id"] for r in result["rules"]["added"]]
    removed_ids = [r["id"] for r in result["rules"]["removed"]]
    assert r2.id in added_ids
    assert r1.id in removed_ids
    assert result["rules"]["retained_ids"] == []


def test_diff_digests_detects_scope_update() -> None:
    r1 = _rule("weatherlib", 1, "use v1")
    r2 = _rule("weatherlib", 2, "use v2")  # same scope, different id

    d1 = Digest(version=1, agent_id="a1", rules=(r1,), skills=(),
                source_event_seq_range=(0, 5))
    d2 = Digest(version=2, agent_id="a1", rules=(r2,), skills=(),
                source_event_seq_range=(5, 10))

    result = diff_digests(d1, d2)
    assert len(result["rules"]["scope_updated"]) == 1
    assert result["rules"]["scope_updated"][0]["scope"] == "weatherlib"


def test_diff_digests_render_diff_counts() -> None:
    d1 = Digest(version=1, agent_id="a1", rules=(), skills=(),
                source_event_seq_range=(0, 1))
    d2 = Digest(version=2, agent_id="a1", rules=(_rule("lib", 2),), skills=(),
                source_event_seq_range=(1, 2))

    result = diff_digests(d1, d2)
    assert result["render_diff"]["to_chars"] > result["render_diff"]["from_chars"]


# ── MnemaClient SDK ───────────────────────────────────────────────────────────

def test_sdk_observe_returns_assertion_id() -> None:
    with MnemaClient(db_path=":memory:", agent_id="agent-1") as client:
        aid = client.observe("weatherlib.get_weather() removed", subject="weatherlib")
        assert aid.startswith("asr_")


def test_sdk_consolidate_requires_llm() -> None:
    with MnemaClient(db_path=":memory:", agent_id="agent-1") as client, \
            pytest.raises(RuntimeError, match="llm"):
        client.consolidate()


def test_sdk_full_lifecycle() -> None:
    llm = FakeLLM.from_scenario("weatherlib_v2")
    with MnemaClient(db_path=":memory:", agent_id="agent-1", llm=llm) as client:
        client.observe("get_weather() removed", subject="weatherlib")
        client.observe("use fetch_forecast(city)", subject="weatherlib")

        digest = client.consolidate()
        assert digest is not None
        assert digest.version == 1

        injected = client.inject()
        assert "## Rules" in injected

        snap = client.snapshot()
        assert snap["latest_digest_version"] == 1
        assert len(snap["active_beliefs"]) == 2


def test_sdk_why_returns_provenance() -> None:
    with MnemaClient(db_path=":memory:", agent_id="agent-1") as client:
        aid = client.observe("some fact", subject="lib")
        result = client.why(aid)
        assert result["assertion_id"] == aid
        assert len(result["wal_events"]) == 1


def test_sdk_timeline_returns_events_for_subject() -> None:
    with MnemaClient(db_path=":memory:", agent_id="agent-1") as client:
        client.observe("fact v1", subject="api")
        client.observe("fact v2", subject="api")
        timeline = client.timeline("api")
        assert len(timeline) >= 2
        assert all(e["seq"] == timeline[i]["seq"] or
                   e["seq"] > timeline[i]["seq"]
                   for i, e in enumerate(timeline[1:], 0))


def test_sdk_retraction_cascade_surfaces_at_risk_rules() -> None:
    llm = FakeLLM.from_scenario("weatherlib_v2")
    with MnemaClient(db_path=":memory:", agent_id="agent-1", llm=llm) as client:
        aid1 = client.observe("get_weather() gone", subject="weatherlib")
        client.observe("use fetch_forecast()", subject="weatherlib")
        client.consolidate()

        at_risk = client.retraction_cascade(aid1)
        assert len(at_risk) >= 1  # the rule derived from aid1 is at risk


def test_sdk_diff_after_two_consolidations() -> None:
    # Use the store directly — each MnemaClient(":memory:") would be a separate DB.
    from graxella.mnema.adapters.sqlite.repository import SqliteMnemaStore
    store = SqliteMnemaStore("sqlite:///:memory:")
    rec = MemoryRecorder(store)
    for _ in range(2):
        rec.observe("agent-1", "v2 fact", subject="lib")
    SleepConsolidator(store, FakeLLM.from_scenario("weatherlib_v2")).consolidate("agent-1")
    for _ in range(2):
        rec.observe("agent-1", "v3 fact", subject="lib")
    SleepConsolidator(store, FakeLLM.from_scenario("weatherlib_v3")).consolidate("agent-1")

    from graxella.mnema.services.auditor import AuditService
    diff = AuditService(store).digest_diff("agent-1", 1, 2)
    assert diff["from_version"] == 1
    assert diff["to_version"] == 2
