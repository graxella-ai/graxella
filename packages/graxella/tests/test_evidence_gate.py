"""Phase 1, tasks 1-1…1-4 — the Evidence Gate.

Property invariants (hypothesis): NO evidence pattern bypasses a hard
block, and NO pattern with successes from fewer than K sessions reaches
AUTO_APPROVE. The signature demo: identical proposal blocked cold, then
auto-approved once cited evidence accrues.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from graxella.beliefs import Memory
from graxella.gate.evidence import (
    MIN_N_FOR_REJECT,
    THR_FLOOR,
    THR_SPAN,
    EvidenceGate,
    GateDecision,
    threshold_for,
)
from graxella.gate.spec import (
    ArtifactKind,
    BlastRadius,
    Proposal,
    ProposalStatus,
    TargetScope,
)


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "mnema.db"), agent_id="gate-t",
                         namespace="refunds")


def seed_outcomes(memory, *, kind="tool_binding", domain="refunds",
                  ok=0, fail=0, sessions=1, model_id=None,
                  chosen="get_weather->fetch_forecast"):
    """Seed the ledger with tuple-scoped outcomes across N sessions."""
    for i in range(ok):
        aid = memory.record_decision(decision_type=kind, task=f"apply {i}",
                                     chosen=chosen, domain=domain)
        memory.record_outcome(decision_id=aid, ok=True, kind=kind,
                              domain=domain, chosen=chosen, model_id=model_id,
                              session_id=f"sess_{i % max(sessions, 1)}")
    for i in range(fail):
        aid = memory.record_decision(decision_type=kind, task=f"apply f{i}",
                                     chosen=chosen, domain=domain)
        memory.record_outcome(decision_id=aid, ok=False, kind=kind,
                              domain=domain, chosen=chosen, model_id=model_id,
                              session_id=f"sess_f{i}")


def proposal(*, domain="refunds", kind=ArtifactKind.TOOL_BINDING,
             blast=BlastRadius.NARROW, tool="get_weather", model_id=None):
    return Proposal(
        kind=kind,
        target=TargetScope(domain=domain, tool=tool, model_id=model_id),
        payload={"replace_skill": "get_weather", "with_skill": "fetch_forecast"},
        origin="miner:rule_distiller",
        blast_radius=blast,
    )


# -- 1-3: the documented threshold curve -------------------------------------

def test_threshold_curve_is_the_documented_formula():
    assert threshold_for(0) == pytest.approx(THR_FLOOR + THR_SPAN)  # 0.95
    assert threshold_for(47) == pytest.approx(0.85 + 0.10 * 2.718281828 ** (-47 / 20.0))
    assert threshold_for(10_000) == pytest.approx(THR_FLOOR, abs=1e-6)
    # Monotone: more confirmed evidence never tightens the threshold.
    curve = [threshold_for(n) for n in range(100)]
    assert curve == sorted(curve, reverse=True)


# -- the signature demo: cold block -> warm auto-approve ---------------------

def test_cold_start_needs_human(memory):
    gate = EvidenceGate(memory)
    verdict, updated = gate.decide(proposal())
    assert verdict.decision is GateDecision.NEEDS_HUMAN
    assert "cold start" in verdict.reason
    assert verdict.prior.n == 0
    assert updated.status is ProposalStatus.NEEDS_HUMAN


def test_same_proposal_auto_approves_after_cited_evidence(memory):
    """The charter's numbers: 47 successes, 2 failures, 9 sessions."""
    gate = EvidenceGate(memory)
    p = proposal()
    v1, _ = gate.decide(p)
    assert v1.decision is GateDecision.NEEDS_HUMAN

    seed_outcomes(memory, ok=47, fail=2, sessions=9)
    gate.refresh()
    v2, updated = gate.decide(p)
    assert v2.decision is GateDecision.AUTO_APPROVE
    assert v2.posterior == pytest.approx(48 / 51, abs=1e-3)   # ≈ 0.94
    assert v2.prior.successes == 47 and v2.prior.failures == 2
    assert v2.prior.sessions == 9
    assert len(v2.prior.citations) > 0
    assert updated.status is ProposalStatus.APPROVED
    # The approval carries its citations (Promotion Spec I2).
    assert len(updated.evidence) > 0


def test_why_is_a_ledger_lookup(memory):
    gate = EvidenceGate(memory)
    p = proposal()
    seed_outcomes(memory, ok=47, fail=2, sessions=9)
    gate.refresh()
    gate.decide(p)
    text = gate.why(p.id)
    assert "AUTO_APPROVE" in text
    assert "47 successes" in text and "9 sessions" in text
    assert "asr_" in text                       # real citations, not prose
    assert "cold-start=NEEDS_HUMAN" in text


def test_verdict_is_itself_a_cited_assertion(memory):
    gate = EvidenceGate(memory)
    seed_outcomes(memory, ok=10, fail=0, sessions=4)
    gate.refresh()
    p = proposal()
    verdict, _ = gate.decide(p)
    rows = memory.beliefs(subject=p.id, predicate="gate_verdict")
    assert len(rows) == 1
    assert rows[0]["id"] == verdict.verdict_assertion_id
    assert rows[0]["object"] == "auto_approve"
    # derived_from carries the evidence the verdict rests on.
    assert set(rows[0]["derived_from"]) == set(verdict.prior.citations)


# -- guards -------------------------------------------------------------------

def test_diversity_floor_blocks_single_session_flood(memory):
    """30 successes from ONE session: great posterior, no approval —
    the evidence-poisoning defense."""
    gate = EvidenceGate(memory, k_diversity=3)
    seed_outcomes(memory, ok=30, fail=0, sessions=1)
    gate.refresh()
    verdict, _ = gate.decide(proposal())
    assert verdict.decision is GateDecision.NEEDS_HUMAN
    assert "diversity" in verdict.reason


def test_wide_blast_needs_overwhelming_evidence(memory):
    gate = EvidenceGate(memory, wide_min_successes=25)
    seed_outcomes(memory, ok=10, fail=0, sessions=4)
    gate.refresh()
    v1, _ = gate.decide(proposal(blast=BlastRadius.WIDE))
    assert v1.decision is GateDecision.NEEDS_HUMAN
    assert "wide blast" in v1.reason

    seed_outcomes(memory, ok=20, fail=0, sessions=4)   # now 30 total
    gate.refresh()
    v2, _ = gate.decide(proposal(blast=BlastRadius.WIDE))
    assert v2.decision is GateDecision.AUTO_APPROVE


def test_clear_failure_history_auto_rejects(memory):
    gate = EvidenceGate(memory)
    seed_outcomes(memory, ok=1, fail=9, sessions=1)
    gate.refresh()
    verdict, updated = gate.decide(proposal())
    assert verdict.decision is GateDecision.AUTO_REJECT
    assert updated.status is ProposalStatus.REJECTED


def test_thin_bad_evidence_asks_human_instead_of_rejecting(memory):
    gate = EvidenceGate(memory)
    seed_outcomes(memory, ok=0, fail=MIN_N_FOR_REJECT - 2, sessions=1)
    gate.refresh()
    verdict, _ = gate.decide(proposal())
    assert verdict.decision is GateDecision.NEEDS_HUMAN


def test_model_scoping_prevents_cross_model_warmth(memory):
    """Evidence under model A never warms the tuple for model B (I4)."""
    gate = EvidenceGate(memory)
    seed_outcomes(memory, ok=40, fail=0, sessions=5, model_id="qwen2.5:3b")
    gate.refresh()
    v_same, _ = gate.decide(proposal(model_id="qwen2.5:3b"))
    v_other, _ = gate.decide(proposal(model_id="claude-sonnet-5"))
    assert v_same.decision is GateDecision.AUTO_APPROVE
    assert v_other.decision is GateDecision.NEEDS_HUMAN
    assert "cold start" in v_other.reason


def test_domain_isolation(memory):
    gate = EvidenceGate(memory)
    seed_outcomes(memory, ok=40, fail=0, sessions=5, domain="refunds")
    gate.refresh()
    v, _ = gate.decide(proposal(domain="healthcare"))
    assert v.decision is GateDecision.NEEDS_HUMAN   # no cross-domain leakage


def test_hard_block_is_final_despite_perfect_evidence(memory):
    gate = EvidenceGate(
        memory,
        hard_blocks=(lambda p: "flow topology change forbidden"
                     if p.payload.get("rewires_flow") else None,),
    )
    seed_outcomes(memory, ok=100, fail=0, sessions=10)
    gate.refresh()
    p = Proposal(kind=ArtifactKind.TOOL_BINDING,
                 target=TargetScope(domain="refunds", tool="get_weather"),
                 payload={"rewires_flow": True},
                 origin="miner:x")
    verdict, updated = gate.decide(p)
    assert verdict.decision is GateDecision.AUTO_REJECT
    assert "hard block" in verdict.reason
    assert updated.status is ProposalStatus.REJECTED


# -- property invariants (hypothesis) ----------------------------------------

@settings(max_examples=15, deadline=None)
@given(ok=st.integers(0, 40), fail=st.integers(0, 10),
       sessions=st.integers(1, 8))
def test_property_no_pattern_bypasses_hard_block(tmp_path_factory, ok, fail, sessions):
    memory = Memory.sqlite(
        str(tmp_path_factory.mktemp("hb") / "m.db"), agent_id="t")
    gate = EvidenceGate(memory, hard_blocks=(lambda p: "always blocked",))
    seed_outcomes(memory, ok=ok, fail=fail, sessions=sessions,
                  domain="default")
    verdict = gate.evaluate(proposal(domain="default"))
    assert verdict.decision is GateDecision.AUTO_REJECT


@settings(max_examples=15, deadline=None)
@given(ok=st.integers(0, 40), fail=st.integers(0, 10))
def test_property_single_session_never_auto_approves(tmp_path_factory, ok, fail):
    memory = Memory.sqlite(
        str(tmp_path_factory.mktemp("dv") / "m.db"), agent_id="t")
    gate = EvidenceGate(memory, k_diversity=3)
    seed_outcomes(memory, ok=ok, fail=fail, sessions=1, domain="default")
    verdict = gate.evaluate(proposal(domain="default"))
    assert verdict.decision is not GateDecision.AUTO_APPROVE
