"""Task 1-5 — one Proposal, one pipeline, no uncited path.

Rulebook promotions, healing recipes, and miner output all flow through
the spec.Proposal lifecycle; the scored GatePolicy is dead; the runtime's
default gate is the EvidenceGate.
"""
from __future__ import annotations

import pytest

import graxella
from graxella.beliefs import Memory
from graxella.exceptions import UnsafeRuleError
from graxella.gate import EvidenceGate, GatePolicy, ObjectiveScores, PromotionGate
from graxella.gate.evidence import GateDecision
from graxella.gate.spec import (
    ArtifactKind,
    EvidenceRole,
    Proposal,
    ProposalStatus,
    TargetScope,
    from_legacy,
)
from graxella.agenda.miners import Proposal as LegacyProposal
from graxella.healing import TransformRecipe
from graxella.rulebook import Rulebook


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "m.db"), agent_id="t",
                         namespace="weather")


def legacy_rule() -> LegacyProposal:
    return LegacyProposal(
        id="prop_weather_sub01",   # miners emit deterministic ids; mirror that
        kind="rule",
        subject="weather:get_weather->fetch_forecast",
        change={"if_intent": "weather", "replace_skill": "get_weather",
                "with_skill": "fetch_forecast",
                "recipe": {"field_map": {"city": "location"}}},
        evidence="2 sessions showed the substitution",
        derived_from=["ep_1", "ep_2"],
        confidence=0.8,
    )


def seed_transform_outcomes(memory, *, ok, sessions, kind="tool_binding"):
    # Evidence contract: outcomes count toward a tuple when their kind
    # equals the ARTIFACT kind — "rule" bridges to tool_binding, so its
    # evidence is recorded under tool_binding.
    for i in range(ok):
        aid = memory.record_decision(decision_type=kind, task=f"heal {i}",
                                     chosen="get_weather", domain="weather")
        memory.record_outcome(decision_id=aid, ok=True, kind=kind,
                              domain="weather", chosen="get_weather",
                              session_id=f"s{i % sessions}")


# -- the bridge --------------------------------------------------------------

def test_from_legacy_maps_the_spec_migration_table():
    p = from_legacy(legacy_rule(), domain="weather")
    assert isinstance(p, Proposal)
    assert p.id == legacy_rule().id            # deterministic id preserved
    assert p.kind is ArtifactKind.TOOL_BINDING  # "rule" migrates
    assert p.target.domain == "weather"
    assert p.target.tool == "get_weather"
    assert p.payload["with_skill"] == "fetch_forecast"
    assert all(c.role is EvidenceRole.EPISODE for c in p.evidence)
    assert {c.assertion_id for c in p.evidence} == {"ep_1", "ep_2"}
    assert p.confidence == 0.8
    assert "2 sessions" in p.note


# -- rulebook: no uncited path ----------------------------------------------

def test_promotion_without_lineage_raises(tmp_path):
    rb = Rulebook(path=tmp_path / "rb.json")
    with pytest.raises(UnsafeRuleError):
        rb.promote(legacy_rule(), approved_by=None)


def test_human_signoff_is_recorded_lineage(tmp_path):
    rb = Rulebook(path=tmp_path / "rb.json")
    rule = rb.promote(legacy_rule(), approved_by="sridhar", domain="weather")
    assert rule.spec_status == "active"
    assert rule.approved_by == "operator:sridhar"
    assert "operator::sridhar" in rule.citations
    assert set(rule.derived_from) == {"ep_1", "ep_2"}   # episode citations
    assert rule.recipe == {"field_map": {"city": "location"}}
    # Idempotent by proposal id.
    again = rb.promote(legacy_rule(), approved_by="someone-else")
    assert again.id == rule.id


def test_gate_auto_approves_warm_promotion_without_a_human(tmp_path, memory):
    rb = Rulebook(path=tmp_path / "rb.json")
    gate = EvidenceGate(memory)
    seed_transform_outcomes(memory, ok=40, sessions=6)
    gate.refresh()
    rule = rb.promote(legacy_rule(), approved_by=None, gate=gate,
                      domain="weather")
    assert rule.spec_status == "active"
    assert rule.approved_by == "gate:evidence"
    assert any(c.startswith("asr_") for c in rule.citations)  # ledger-cited
    # And the verdict is in the ledger: why() works for this promotion.
    assert "AUTO_APPROVE" in gate.why(rule.proposal_id)


def test_gate_cold_plus_human_supplements(tmp_path, memory):
    rb = Rulebook(path=tmp_path / "rb.json")
    gate = EvidenceGate(memory)
    rule = rb.promote(legacy_rule(), approved_by="sridhar", gate=gate,
                      domain="weather")
    assert rule.spec_status == "active"
    assert rule.approved_by == "operator:sridhar"
    # Cold verdict recorded; pending queue saw and released it.
    assert "NEEDS_HUMAN" in gate.why(rule.proposal_id)


def test_gate_cold_without_human_raises(tmp_path, memory):
    rb = Rulebook(path=tmp_path / "rb.json")
    gate = EvidenceGate(memory)
    with pytest.raises(UnsafeRuleError):
        rb.promote(legacy_rule(), approved_by=None, gate=gate,
                   domain="weather")


def test_gate_rejection_is_final(tmp_path, memory):
    rb = Rulebook(path=tmp_path / "rb.json")
    gate = EvidenceGate(memory)
    # Clear failure history for the tuple.
    for i in range(8):
        aid = memory.record_decision(decision_type="tool_binding", task=f"h{i}",
                                     chosen="get_weather", domain="weather")
        memory.record_outcome(decision_id=aid, ok=False, kind="tool_binding",
                              domain="weather", chosen="get_weather",
                              session_id=f"s{i}")
    gate.refresh()
    with pytest.raises(UnsafeRuleError, match="rejected"):
        rb.promote(legacy_rule(), approved_by="sridhar", gate=gate,
                   domain="weather")


# -- healing recipes join the pipeline ---------------------------------------

def test_transform_recipe_ships_as_spec_proposal():
    recipe = TransformRecipe(field_map={"city": "location"},
                             drop_fields=("legacy_flag",))
    p = recipe.to_proposal(domain="weather", tool="get_weather",
                           origin="healer:brownbrillion")
    assert p.kind is ArtifactKind.TRANSFORM
    assert p.payload["field_map"] == {"city": "location"}
    assert p.target.tool == "get_weather"
    # Deterministic: same recipe + target => same proposal id.
    assert p.id == recipe.to_proposal(domain="weather", tool="get_weather",
                                      origin="healer:x").id


# -- the scored gate is dead --------------------------------------------------

def test_gatepolicy_and_objectivescores_are_gone():
    with pytest.raises(RuntimeError, match="EvidenceGate"):
        GatePolicy()
    with pytest.raises(RuntimeError, match="removed"):
        ObjectiveScores(quality=0.9)


def test_promotiongate_is_a_deprecated_human_queue():
    with pytest.warns(DeprecationWarning):
        q = PromotionGate()
    p = q.propose("rule.new", {"x": 1})
    assert q.evaluate(p.id).value == "needs_human"     # nothing automatic
    q.approve(p.id, by="human")
    q.activate(p.id, by="human")
    assert q.get(p.id).status.value == "active"


# -- the runtime's default gate is the EvidenceGate --------------------------

def test_instrumented_app_defaults_to_evidence_gate(tmp_path, memory):
    def billing_agent(payload):
        """decide refund eligibility for billing complaints"""
        return {"result": f"ok {payload}"}

    app = graxella.mesh([billing_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"), domain="weather")
    assert isinstance(app.gate, EvidenceGate)
    # The gate reads the SAME ledger route() writes outcomes to.
    app.route("billing refund order 9")
    app.gate.refresh()
    assert app.gate.memory is app.memory
    # Gate verdicts flow into the unified tracer as source="gate".
    proposal = Proposal(kind=ArtifactKind.TOOL_BINDING,
                        target=TargetScope(domain="weather"),
                        payload={}, origin="miner:t")
    verdict, _ = app.gate.decide(proposal)
    events = app.tracer.events(source="gate", event_type="verdict")
    assert len(events) == 1
    assert events[0].payload["decision"] == verdict.decision.value
