"""SQLite adapter tests for the ConsolidationRepository (S2.1).

Covers: digest save roundtrip, CONSOLIDATION_RUN event emission, supersession
by scope/name across digest versions, time-travel via get_digest(v),
mark_skill_outcome as non-WAL metric update.
"""

from __future__ import annotations

import pytest

from mnema.adapters.sqlite.repository import SqliteMnemaStore
from mnema.core.consolidation import Digest, Rule, Skill
from mnema.core.events import EventType


def _rule(scope: str, dv: int, conf: float = 0.9, text: str = "use v2") -> Rule:
    return Rule(
        text=text,
        scope=scope,
        derived_from=(f"asrt_{scope}",),
        confidence=conf,
        digest_version=dv,
    )


def _skill(name: str, dv: int) -> Skill:
    return Skill(
        name=name,
        description=f"{name} skill",
        code=f"def {name}(x): return x + 1\n",
        signature=f"def {name}(x: int) -> int",
        derived_from=(f"asrt_{name}",),
        digest_version=dv,
    )


def test_save_digest_roundtrip(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    d = Digest(
        version=1,
        agent_id="agent-1",
        rules=(_rule("weatherlib", 1),),
        skills=(_skill("fetch_forecast", 1),),
        source_event_seq_range=(0, 5),
    )
    store.save_digest(d)

    got = store.latest_digest(agent_id="agent-1")
    assert got is not None
    assert got.version == 1
    assert got.agent_id == "agent-1"
    assert {r.id for r in got.rules} == {r.id for r in d.rules}
    assert {s.id for s in got.skills} == {s.id for s in d.skills}


def test_save_digest_emits_consolidation_run_event(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    d = Digest(
        version=1,
        agent_id="agent-1",
        rules=(_rule("weatherlib", 1),),
        skills=(),
        source_event_seq_range=(0, 3),
    )
    store.save_digest(d)

    events = list(store.read())
    assert len(events) == 1
    seq, evt = events[0]
    assert seq == 1
    assert evt.event_type is EventType.CONSOLIDATION_RUN
    assert evt.agent_id == "agent-1"
    assert evt.payload["version"] == 1
    assert evt.payload["source_seq_range"] == [0, 3]
    assert evt.payload["rule_ids"] == [d.rules[0].id]


def test_supersession_by_scope_across_digest_versions(tmp_path) -> None:
    """A new digest with a rule for the same scope supersedes the older rule."""
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")

    d1 = Digest(
        version=1,
        agent_id="agent-1",
        rules=(_rule("weatherlib", 1, text="use v1"),),
        skills=(),
        source_event_seq_range=(0, 5),
    )
    store.save_digest(d1)

    d2 = Digest(
        version=2,
        agent_id="agent-1",
        rules=(_rule("weatherlib", 2, text="use v2"),),
        skills=(),
        source_event_seq_range=(5, 10),
    )
    store.save_digest(d2)

    # Latest = only the v2 rule is active.
    active = store.active_rules(agent_id="agent-1")
    assert [r.text for r in active] == ["use v2"]

    # Time travel: at v1, the old rule was active and not superseded.
    hydrated_v1 = store.get_digest(1, agent_id="agent-1")
    assert hydrated_v1 is not None
    assert [r.text for r in hydrated_v1.rules] == ["use v1"]

    hydrated_v2 = store.get_digest(2, agent_id="agent-1")
    assert hydrated_v2 is not None
    assert [r.text for r in hydrated_v2.rules] == ["use v2"]


def test_supersession_by_skill_name(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    d1 = Digest(
        version=1,
        agent_id="agent-1",
        rules=(),
        skills=(_skill("fetch_forecast", 1),),
        source_event_seq_range=(0, 5),
    )
    store.save_digest(d1)

    d2 = Digest(
        version=2,
        agent_id="agent-1",
        rules=(),
        skills=(_skill("fetch_forecast", 2),),
        source_event_seq_range=(5, 10),
    )
    store.save_digest(d2)

    active = store.active_skills(agent_id="agent-1")
    assert len(active) == 1
    assert active[0].digest_version == 2

    # Both skills preserved for time-travel.
    at_v1 = store.get_digest(1, agent_id="agent-1")
    at_v2 = store.get_digest(2, agent_id="agent-1")
    assert at_v1 is not None and at_v2 is not None
    assert at_v1.skills[0].digest_version == 1
    assert at_v2.skills[0].digest_version == 2


def test_latest_digest_returns_none_when_empty(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    assert store.latest_digest(agent_id="agent-1") is None


def test_get_digest_returns_none_for_missing_version(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    d = Digest(
        version=1,
        agent_id="agent-1",
        rules=(),
        skills=(),
        source_event_seq_range=(0, 1),
    )
    store.save_digest(d)
    assert store.get_digest(99, agent_id="agent-1") is None


def test_mark_skill_outcome_is_not_wal_tracked(tmp_path) -> None:
    """Skill outcomes are metrics, not beliefs (ADR-0002). No new event."""
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    d = Digest(
        version=1,
        agent_id="agent-1",
        rules=(),
        skills=(_skill("fetch_forecast", 1),),
        source_event_seq_range=(0, 1),
    )
    store.save_digest(d)
    events_before = list(store.read())
    assert len(events_before) == 1  # Only CONSOLIDATION_RUN

    sk_id = d.skills[0].id
    store.mark_skill_outcome(sk_id, success=True)
    store.mark_skill_outcome(sk_id, success=True)
    store.mark_skill_outcome(sk_id, success=False)

    events_after = list(store.read())
    assert len(events_after) == 1  # STILL only CONSOLIDATION_RUN — no metric event.

    active = store.active_skills(agent_id="agent-1")
    assert active[0].success_count == 2
    assert active[0].failure_count == 1


def test_mark_skill_outcome_unknown_id_raises(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    with pytest.raises(KeyError):
        store.mark_skill_outcome("skl_missing", success=True)


def test_multi_agent_namespacing_isolates_digests(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    a1 = Digest(
        version=1,
        agent_id="agent-1",
        rules=(_rule("s", 1, text="a1 rule"),),
        skills=(),
        source_event_seq_range=(0, 1),
    )
    a2 = Digest(
        version=1,
        agent_id="agent-2",
        rules=(_rule("s", 1, text="a2 rule"),),
        skills=(),
        source_event_seq_range=(0, 1),
    )
    store.save_digest(a1)
    store.save_digest(a2)

    a1_rules = store.active_rules(agent_id="agent-1")
    a2_rules = store.active_rules(agent_id="agent-2")
    assert [r.text for r in a1_rules] == ["a1 rule"]
    assert [r.text for r in a2_rules] == ["a2 rule"]


def test_active_rules_as_of_version(tmp_path) -> None:
    store = SqliteMnemaStore(f"sqlite:///{tmp_path}/m.db")
    d1 = Digest(
        version=1,
        agent_id="agent-1",
        rules=(_rule("weatherlib", 1, text="use v1"),),
        skills=(),
        source_event_seq_range=(0, 5),
    )
    store.save_digest(d1)
    d2 = Digest(
        version=2,
        agent_id="agent-1",
        rules=(_rule("weatherlib", 2, text="use v2"),),
        skills=(),
        source_event_seq_range=(5, 10),
    )
    store.save_digest(d2)

    at_v1 = store.active_rules(agent_id="agent-1", as_of_version=1)
    assert [r.text for r in at_v1] == ["use v1"]

    at_v2 = store.active_rules(agent_id="agent-1", as_of_version=2)
    assert [r.text for r in at_v2] == ["use v2"]
