"""Forward provenance (touched edges): 'what did this decision affect?' and
'what touched this entity?' -- plus the heal auto-edge. Pure + deterministic.
"""
from __future__ import annotations

import graxella
from graxella.beliefs import Memory


def _mem() -> Memory:
    return Memory.sqlite(":memory:", agent_id="t", namespace="support")


def test_touched_forward_and_reverse():
    m = _mem()
    d1 = m.record_decision(decision_type="delegate", task="refund order 1234",
                           chosen="refunds_desk", domain="support")
    d2 = m.record_decision(decision_type="delegate", task="ship order 1234",
                           chosen="orders_desk", domain="support")
    m.record_touch(d1, "order:1234", role="entity")
    m.record_touch(d1, "customer:c-9", role="entity")
    m.record_touch(d2, "order:1234", role="entity")

    # forward: what did d1 affect?
    assert {t["target"] for t in m.touched_by(d1)} == {"order:1234", "customer:c-9"}
    # reverse: everything that touched the order, across both agents
    touchers = m.touching("order:1234")
    assert {t["decision_id"] for t in touchers} == {d1, d2}


def test_provenance_combines_both_directions():
    m = _mem()
    d = m.record_decision(decision_type="tool", task="call", chosen="ship",
                          domain="support")
    m.record_outcome(decision_id=d, ok=True, kind="tool", chosen="ship",
                     domain="support", session_id="s0")
    m.record_touch(d, "order:99", role="entity")
    o = m.beliefs(subject=d, predicate="outcome")[0]

    prov = m.provenance(o["id"])            # an outcome derived_from the decision
    assert d in prov["derived_from"]        # backward evidence
    prov_d = m.provenance(d)
    assert {t["target"] for t in prov_d["touched"]} == {"order:99"}  # forward


def test_heal_auto_records_proposal_touch(monkeypatch):
    """A drift heal auto-links its decision to the proposal it created, with
    no developer code -- so an incident is traceable to the rule it spawned."""
    def fake_build_default_healer(model_id=None, **_):
        def healer(tool_name, args, error):
            return graxella.TransformRecipe(field_map={"order_id": "order_ref"})
        return healer
    monkeypatch.setattr("graxella.healing.dspy_healer.build_default_healer",
                        fake_build_default_healer)

    grx = graxella.Session("t", domain="ship", model_id="m", workdir="ephemeral")

    def v2(a: dict) -> str:
        return f"ok {a['order_ref']}"

    @grx.tool(name="ship_status", fallback=v2)
    def ship(order_id: str) -> str:
        """ship"""
        raise TypeError("unexpected keyword argument 'order_id'; schema "
                        "deprecated - use 'order_ref' instead")

    ship.invoke({"order_id": "1"})          # heal -> proposal + touch edge

    pend = grx.pending()
    assert pend, "expected a proposal"
    pid = pend[0]["proposal_id"]
    # reverse-provenance: which decision(s) produced this proposal?
    origin = grx.memory.touching(pid)
    assert origin, "heal decision should touch the proposal it created"
    assert origin[0]["role"] == "proposal"
