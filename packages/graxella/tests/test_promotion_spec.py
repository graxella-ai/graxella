"""Promotion Spec v0.1 — invariant tests (build plan task S-1).

I1 immutability · I2 evidence-required-for-promotion · I3 transition
state machine · I4 model-scoped gate key · deterministic miner ids ·
JSON-safe rendering · wiring into gate/healing/agenda.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from graxella.gate.spec import (
    SPEC_VERSION,
    ArtifactKind,
    BlastRadius,
    EvidenceCitation,
    EvidenceRole,
    InvalidTransitionError,
    Proposal,
    ProposalStatus,
    TargetScope,
    TRANSITIONS,
)


def _target(**kw) -> TargetScope:
    return TargetScope(domain="refunds", **kw)


def _proposal(**kw) -> Proposal:
    defaults = dict(
        kind=ArtifactKind.TOOL_BINDING,
        target=_target(),
        payload={"replace_skill": "get_weather", "with_skill": "fetch_forecast"},
        origin="miner:rule_distiller",
    )
    defaults.update(kw)
    return Proposal(**defaults)


def _cite(aid: str = "asr_abc123", role: EvidenceRole = EvidenceRole.EPISODE):
    return EvidenceCitation(assertion_id=aid, role=role)


# -- construction & validation ---------------------------------------------

def test_minimal_proposal_defaults():
    p = _proposal()
    assert p.status is ProposalStatus.PENDING
    assert p.spec_version == SPEC_VERSION
    assert p.version == 1
    assert p.blast_radius is BlastRadius.UNKNOWN
    assert p.evidence == ()
    assert p.id.startswith("prop_")


def test_domain_is_mandatory():
    with pytest.raises(ValidationError):
        TargetScope(domain="")


def test_origin_is_mandatory():
    with pytest.raises(ValidationError):
        _proposal(origin="")


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        _proposal(confidence=1.5)
    assert _proposal(confidence=0.9).confidence == 0.9


# -- I1: immutability -------------------------------------------------------

def test_proposal_is_frozen():
    p = _proposal()
    with pytest.raises(ValidationError):
        p.status = ProposalStatus.ACTIVE  # type: ignore[misc]


def test_with_status_returns_new_object_same_id():
    p = _proposal()
    p2 = p.with_status(ProposalStatus.NEEDS_HUMAN, by="gate:cold_start")
    assert p2 is not p
    assert p2.id == p.id
    assert p.status is ProposalStatus.PENDING          # audit record untouched
    assert p2.status is ProposalStatus.NEEDS_HUMAN
    assert p2.decided_by == "gate:cold_start"
    assert p2.decided_at is not None


# -- I2: every promotion cites ---------------------------------------------

def test_cannot_construct_approved_without_evidence():
    with pytest.raises(ValidationError):
        _proposal(status=ProposalStatus.APPROVED)


def test_cannot_transition_to_approved_without_evidence():
    p = _proposal()
    with pytest.raises(ValidationError):
        p.with_status(ProposalStatus.APPROVED, by="operator:sridhar")


def test_operator_signoff_is_a_citation_not_an_exemption():
    p = _proposal()
    approved = p.with_status(
        ProposalStatus.APPROVED, by="operator:sridhar",
        extra_evidence=(_cite("asr_op1", EvidenceRole.OPERATOR_DECISION),),
    )
    assert approved.status is ProposalStatus.APPROVED
    assert approved.evidence[-1].role is EvidenceRole.OPERATOR_DECISION


# -- I3: state machine ------------------------------------------------------

def test_full_valid_lifecycle():
    p = _proposal(evidence=(_cite(),))
    p = p.with_status(ProposalStatus.NEEDS_HUMAN, by="gate:cold_start")
    p = p.with_status(ProposalStatus.APPROVED, by="operator:sridhar",
                      extra_evidence=(_cite("asr_op", EvidenceRole.OPERATOR_DECISION),))
    p = p.with_status(ProposalStatus.ACTIVE, by="gate")
    p = p.with_status(ProposalStatus.SUPERSEDED, by="gate:v2_promoted")
    assert p.status is ProposalStatus.SUPERSEDED


@pytest.mark.parametrize("start,attempt", [
    (ProposalStatus.PENDING, ProposalStatus.ACTIVE),        # must pass approval
    (ProposalStatus.PENDING, ProposalStatus.SUPERSEDED),
    (ProposalStatus.NEEDS_HUMAN, ProposalStatus.ACTIVE),
    (ProposalStatus.ACTIVE, ProposalStatus.APPROVED),       # no going back
    (ProposalStatus.REJECTED, ProposalStatus.APPROVED),     # terminal
    (ProposalStatus.ROLLED_BACK, ProposalStatus.ACTIVE),    # terminal
    (ProposalStatus.SUPERSEDED, ProposalStatus.ACTIVE),     # terminal
])
def test_invalid_transitions_raise(start, attempt):
    evidence = (_cite(),) if start in (ProposalStatus.APPROVED,
                                       ProposalStatus.ACTIVE,
                                       ProposalStatus.ROLLED_BACK,
                                       ProposalStatus.SUPERSEDED,
                                       ProposalStatus.REJECTED) else ()
    p = _proposal(status=start, evidence=evidence or (_cite(),)) \
        if start is not ProposalStatus.PENDING else _proposal()
    with pytest.raises(InvalidTransitionError):
        p.with_status(attempt, by="test")


def test_terminal_states_have_no_exits():
    for terminal in (ProposalStatus.REJECTED, ProposalStatus.ROLLED_BACK,
                     ProposalStatus.SUPERSEDED):
        assert TRANSITIONS[terminal] == frozenset()


# -- I4: gate key is model-scoped ------------------------------------------

def test_gate_key_includes_model_id():
    t1 = _target(model_id="qwen2.5:3b")
    t2 = _target(model_id="claude-sonnet-5")
    k1 = t1.gate_key(ArtifactKind.PROMPT)
    k2 = t2.gate_key(ArtifactKind.PROMPT)
    assert k1 != k2
    assert k1[0] == "refunds" and k1[1] == "prompt"


# -- deterministic ids ------------------------------------------------------

def test_deterministic_id_stable_and_sensitive():
    t = _target(agent="triage")
    payload = {"replace_skill": "a", "with_skill": "b"}
    id1 = Proposal.deterministic_id(ArtifactKind.TOOL_BINDING, t, payload)
    id2 = Proposal.deterministic_id(ArtifactKind.TOOL_BINDING, t, payload)
    id3 = Proposal.deterministic_id(ArtifactKind.TOOL_BINDING, t,
                                    {**payload, "with_skill": "c"})
    assert id1 == id2
    assert id1 != id3
    assert id1.startswith("prop_")


# -- rendering --------------------------------------------------------------

def test_to_payload_is_json_safe():
    p = _proposal(evidence=(_cite(),), confidence=0.8)
    rendered = json.dumps(p.to_payload())          # must not raise
    round_tripped = json.loads(rendered)
    assert round_tripped["kind"] == "tool_binding"
    assert round_tripped["target"]["domain"] == "refunds"
    assert round_tripped["evidence"][0]["role"] == "episode"


# -- versioning / rollback fields ------------------------------------------

def test_supersedes_and_rollback_fields():
    v1 = _proposal()
    v2 = _proposal(version=2, supersedes=v1.id)
    rb = _proposal(version=3, rollback_of=v2.id, payload=v1.payload)
    assert v2.supersedes == v1.id
    assert rb.rollback_of == v2.id
    assert rb.payload == v1.payload


# -- wiring (S-1 definition of done) ---------------------------------------

def test_spec_is_canonical_in_gate_package():
    from graxella.gate import spec
    assert spec.Proposal is Proposal


def test_healing_and_agenda_import_the_spec():
    import graxella.agenda as agenda
    import graxella.healing as healing
    assert agenda.promotion_spec.Proposal is Proposal
    assert healing.promotion_spec.Proposal is Proposal


def test_legacy_proposals_still_importable_but_marked():
    from graxella.agenda.miners import Proposal as LegacyMinerProposal
    from graxella.gate.promoter import Proposal as LegacyGateProposal
    assert "deprecated" in (LegacyMinerProposal.__doc__ or "").lower()
    assert LegacyGateProposal is not Proposal
