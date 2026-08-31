"""Un-learning: a promoted rule whose recent evidence turns bad is
demoted -- reconcile() closes the evidence loop in both directions.

Answers the standing "isn't this just hardcoding?" critique with a
mechanism, not an assurance: a promoted rule is discovered, learned,
served, and — if it stops working — un-learned. No LLM in the decision.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import graxella
from graxella.gate.health import RuleHealth, rule_health, should_demote
from graxella.gate.spec import ArtifactKind, Proposal, TargetScope


def _promote_ship_rule(grx) -> str:
    """Directly promote a transform rule for 'ship' (bypassing the heal
    ladder — we only need an ACTIVE rule to demote)."""
    payload = {"field_map": {"order_id": "order_ref"}}
    target = TargetScope(domain="ship", tool="ship")
    p = Proposal(id=Proposal.deterministic_id(ArtifactKind.TRANSFORM,
                                              target, payload),
                 kind=ArtifactKind.TRANSFORM, target=target,
                 payload=payload, origin="test")
    rule = grx.rulebook.promote(p, approved_by="operator:test")
    return rule.id


def _session(work) -> graxella.Session:
    return graxella.Session("t", domain="ship", model_id="m", workdir=work)


# ------------------------------------------------------- health primitives

def test_should_demote_thresholds():
    thin = RuleHealth(rule_id="r", successes=0, failures=2)
    demote, reason = should_demote(thin)
    assert not demote and "floor" in reason        # too little evidence

    bad = RuleHealth(rule_id="r", successes=1, failures=3)  # posterior 2/6=.33
    demote, reason = should_demote(bad)
    assert demote and "posterior" in reason

    good = RuleHealth(rule_id="r", successes=10, failures=1)
    demote, reason = should_demote(good)
    assert not demote


def test_rule_health_reads_exact_id_scoped_touches():
    """No cross-rule bleed: a differently-named rule's touches must not
    contaminate this rule's health (the P0-1 lesson, applied here)."""
    work = Path(tempfile.mkdtemp(prefix="demote-health-"))
    grx = _session(work)
    aid = grx.memory.record_decision(decision_type="tool", task="x",
                                     chosen="ship", domain="ship")
    grx.memory.record_touch(aid, "apr_target", role="rule_use",
                            detail={"ok": True})
    grx.memory.record_touch(aid, "apr_target_v2", role="rule_use",
                            detail={"ok": False})   # different rule id

    h = rule_health(grx.memory, "apr_target")
    assert h.successes == 1 and h.failures == 0


# ------------------------------------------------------------- reconcile()

def test_reconcile_demotes_a_failing_promoted_rule():
    work = Path(tempfile.mkdtemp(prefix="demote-e2e-"))
    grx = _session(work)
    rule_id = _promote_ship_rule(grx)
    assert grx.rulebook.find_substitution("ship") is not None

    # simulate this rule failing repeatedly in production (rung-2 style
    # touches, exactly what the interceptor now writes)
    for i in range(4):
        aid = grx.memory.record_decision(decision_type="tool", task="call",
                                         chosen="ship", domain="ship")
        grx.memory.record_touch(aid, rule_id, role="rule_use",
                                detail={"ok": False})

    result = grx.reconcile()
    assert any(r.id == rule_id for r in result.demoted)
    assert grx.rulebook.find_substitution("ship") is None   # stops serving

    demoted_rule = next(r for r in grx.rulebook.all_rules() if r.id == rule_id)
    assert demoted_rule.status == "rolled_back"
    assert demoted_rule.demoted_by == "gate:evidence"
    assert "posterior" in demoted_rule.demoted_reason


def test_reconcile_leaves_a_healthy_rule_active():
    work = Path(tempfile.mkdtemp(prefix="demote-healthy-"))
    grx = _session(work)
    rule_id = _promote_ship_rule(grx)
    for i in range(6):
        aid = grx.memory.record_decision(decision_type="tool", task="call",
                                         chosen="ship", domain="ship")
        grx.memory.record_touch(aid, rule_id, role="rule_use",
                                detail={"ok": True})

    result = grx.reconcile()
    assert result.demoted == []
    assert grx.rulebook.find_substitution("ship") is not None


def test_demoted_rule_no_longer_dispatches_end_to_end():
    """A demoted rule stops intercepting drift -- the tool falls through
    to the healer again (rung 3), not the dead rule (rung 2)."""
    work = Path(tempfile.mkdtemp(prefix="demote-e2e2-"))
    grx = _session(work)
    rule_id = _promote_ship_rule(grx)
    grx.rulebook.demote(rule_id, by="operator:test", reason="test demotion")

    calls = {"n": 0}

    def v2(args: dict) -> str:
        calls["n"] += 1
        return f"ok {args['order_ref']}"

    @grx.healer
    def h(tool_name, args, error):
        return graxella.TransformRecipe(field_map={"order_id": "order_ref"})

    @grx.tool(name="ship", fallback=v2)
    def ship(order_id: str) -> str:
        """ship"""
        raise TypeError("unexpected keyword argument 'order_id'; schema "
                        "deprecated - use 'order_ref'")

    # rung 2 is dead (rule demoted) -> falls to rung 3 (healer), heals again
    assert ship.invoke({"order_id": "1"}) == "ok 1"
    assert calls["n"] == 1


def test_demote_is_idempotent():
    work = Path(tempfile.mkdtemp(prefix="demote-idem-"))
    grx = _session(work)
    rule_id = _promote_ship_rule(grx)
    r1 = grx.rulebook.demote(rule_id, by="a", reason="first")
    r2 = grx.rulebook.demote(rule_id, by="b", reason="second")
    assert r1.demoted_by == "a" and r2.demoted_by == "a"   # unchanged, no-op
