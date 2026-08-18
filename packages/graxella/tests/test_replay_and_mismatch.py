"""Tasks 1-6 (paired-replay evidence) and 1-7 (mismatch detection+miner)."""
from __future__ import annotations

import pytest

import graxella
from graxella.beliefs import Memory
from graxella.gate.audit import (
    ReplayCase,
    audit,
    replay_counts_for,
    with_replay_evidence,
)
from graxella.gate.evidence import EvidenceGate, GateDecision
from graxella.gate.spec import ArtifactKind, EvidenceRole, Proposal, TargetScope
from graxella.agenda.mismatch import MismatchMiner
from graxella.healing import TransformRecipe


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "m.db"), agent_id="t",
                         namespace="weather")


def transform_proposal() -> Proposal:
    return TransformRecipe(field_map={"city": "location"}).to_proposal(
        domain="weather", tool="get_weather", origin="healer:test")


def cases(n=3, expected_key="location"):
    return [ReplayCase(case_id=f"c{i}",
                       inputs={"city": f"Town{i}"},
                       expected={expected_key: f"Town{i}"},
                       source_ids=(f"ep_{i}",))
            for i in range(n)]


# -- 1-6: the auditor ---------------------------------------------------------

def test_good_candidate_wins_all_cases(memory):
    report = audit(transform_proposal(), cases(3), memory=memory)
    assert report.wins == 3 and report.losses == 0
    assert report.assertion_id is not None
    row = memory.beliefs(subject=report.proposal_id,
                         predicate="paired_replay")[0]
    assert row["object"] == "3/3"
    assert set(row["derived_from"]) == {"ep_0", "ep_1", "ep_2"}


def test_bad_candidate_loses_and_exceptions_are_losses(memory):
    bad = Proposal(kind=ArtifactKind.TRANSFORM,
                   target=TargetScope(domain="weather", tool="get_weather"),
                   payload={"field_map": {"city": "town"}},  # wrong rename
                   origin="healer:test")
    report = audit(bad, cases(2), memory=memory)
    assert report.wins == 0 and report.losses == 2
    assert all("differs" in r.note for r in report.results)

    def exploding(_p, _i):
        raise ValueError("boom")
    report2 = audit(bad, cases(1), apply_fn=exploding)
    assert report2.losses == 1
    assert "ValueError" in report2.results[0].note


def test_replay_citation_attaches_to_proposal(memory):
    p = transform_proposal()
    report = audit(p, cases(3), memory=memory)
    p2 = with_replay_evidence(p, report)
    assert any(c.role is EvidenceRole.PAIRED_REPLAY for c in p2.evidence)
    assert replay_counts_for(memory, p.id) == (3, 0)


# -- 1-6: gate fusion ---------------------------------------------------------

def seed(memory, *, ok, fail, sessions, kind="transform"):
    for i in range(ok):
        aid = memory.record_decision(decision_type=kind, task=f"a{i}",
                                     chosen="get_weather", domain="weather")
        memory.record_outcome(decision_id=aid, ok=True, kind=kind,
                              domain="weather", chosen="get_weather",
                              session_id=f"s{i % sessions}")
    for i in range(fail):
        aid = memory.record_decision(decision_type=kind, task=f"f{i}",
                                     chosen="get_weather", domain="weather")
        memory.record_outcome(decision_id=aid, ok=False, kind=kind,
                              domain="weather", chosen="get_weather",
                              session_id="sf")


def test_replay_tips_a_borderline_tuple(memory):
    """6 ok / 1 fail over 4 sessions: posterior 0.778 < thr(6)=0.924 —
    NEEDS_HUMAN. A 20/0 replay table lifts the fused posterior to 0.931,
    over the (unchanged) operational threshold: AUTO_APPROVE."""
    gate = EvidenceGate(memory)
    p = transform_proposal()
    seed(memory, ok=6, fail=1, sessions=4)
    gate.refresh()
    v1 = gate.evaluate(p)
    assert v1.decision is GateDecision.NEEDS_HUMAN

    audit(p, cases(20), memory=memory)
    v2 = gate.evaluate(p)
    assert v2.prior.replay_wins == 20
    assert v2.decision is GateDecision.AUTO_APPROVE
    assert v2.threshold == v1.threshold        # thr calibrates on ops only
    assert any("replay 20/20" in g for g in v2.guards)


def test_replay_alone_never_auto_approves_cold_tuple(memory):
    """A perfect replay table on a COLD tuple still needs a human —
    replay is a single source; diversity counts operational sessions."""
    gate = EvidenceGate(memory)
    p = transform_proposal()
    audit(p, cases(50), memory=memory)
    verdict = gate.evaluate(p)
    assert verdict.decision is GateDecision.NEEDS_HUMAN
    assert verdict.prior.replay_wins == 50     # ...but the human SEES it


# -- 1-7: live detector -------------------------------------------------------

def claimy_agent(payload):
    """decide refund eligibility for billing complaints and orders"""
    return {"result": "I checked the order and it qualifies for a refund.",
            "tool_calls": []}


def honest_agent(payload):
    """write a friendly response email to the customer"""
    return {"result": "Here is a draft email for the customer."}


def test_mismatch_detected_and_recorded(tmp_path, memory):
    app = graxella.mesh([claimy_agent, honest_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"), domain="weather")
    _, aid = app.route("billing refund order 12")
    events = app.tracer.events(event_type="governance.reasoning_action_mismatch")
    assert len(events) == 1
    assert events[0].payload["claim"] == "checked"
    sigs = memory.signals(kind="reasoning_action_mismatch")
    assert len(sigs) == 1 and sigs[0]["decision_id"] == aid


def test_no_mismatch_when_tools_were_used(tmp_path, memory):
    def tooled_agent(payload):
        """decide refund eligibility for billing complaints and orders"""
        return {"result": "I checked the order via the lookup tool.",
                "tool_calls": [{"name": "check_order", "args": {}}]}

    app = graxella.mesh([tooled_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"), domain="weather")
    _, aid = app.route("billing refund order 12")
    assert memory.signals(kind="reasoning_action_mismatch") == []
    # And the tool trail landed on the outcome record.
    assert memory.outcomes_for(aid)[0].tools_used == ["check_order"]


def test_plain_answers_are_not_flagged(tmp_path, memory):
    app = graxella.mesh([honest_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"), domain="weather")
    app.route("draft the friendly email")
    assert memory.signals(kind="reasoning_action_mismatch") == []


# -- 1-7: the miner -----------------------------------------------------------

def test_repeated_mismatches_become_a_gated_prompt_proposal(tmp_path, memory):
    app = graxella.mesh([claimy_agent, honest_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"), domain="weather")
    app.route("billing refund order 1")
    app.route("billing refund order 2")

    miner = MismatchMiner(memory)
    proposals = miner.mine()
    assert len(proposals) == 1
    p = proposals[0]
    assert p.kind is ArtifactKind.PROMPT
    assert p.target.agent == "claimy_agent"
    assert p.target.domain == "weather"
    assert p.payload["occurrences"] == 2
    assert "checked" in p.payload["claims"]
    assert len(p.evidence) == 2                    # one citation per signal
    assert all(c.assertion_id.startswith("asr_") for c in p.evidence)
    # Deterministic: re-mining yields the same proposal id.
    assert miner.mine()[0].id == p.id


def test_single_occurrence_is_below_support(tmp_path, memory):
    app = graxella.mesh([claimy_agent, honest_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"), domain="weather")
    app.route("billing refund order 1")
    assert MismatchMiner(memory).mine() == []
