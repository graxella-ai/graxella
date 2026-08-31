"""Regression: self-certified replay must not inflate the gate posterior.

External probe (2026-08-30): the healer's heal-time replay case used the
recipe's own output as ``expected``, so the auditor inevitably matched --
and that tautological "1/1 wins" was fused into the posterior as replay
evidence. Now: heal-time reports are marked self_certified, stay visible
as reviewer citations, and are EXCLUDED from posterior fusion.
Independent replay (expected from matched historical successes) still
fuses.
"""
from __future__ import annotations

import graxella
from graxella.beliefs import Memory
from graxella.gate.audit import ReplayCase, audit, replay_counts_for
from graxella.gate.spec import ArtifactKind, Proposal, TargetScope


def _proposal() -> Proposal:
    payload = {"field_map": {"a": "b"}, "static_defaults": {}, "drop_fields": []}
    target = TargetScope(domain="d", tool="t")
    return Proposal(id=Proposal.deterministic_id(ArtifactKind.TRANSFORM,
                                                 target, payload),
                    kind=ArtifactKind.TRANSFORM, target=target,
                    payload=payload, origin="test")


def test_self_certified_report_not_fused():
    mem = Memory.sqlite(":memory:", agent_id="t", namespace="d")
    p = _proposal()
    # the tautology: expected produced by the recipe under audit
    audit(p, [ReplayCase(case_id="live::x", inputs={"a": 1},
                         expected={"b": 1})],
          memory=mem, self_certified=True)
    assert replay_counts_for(mem, p.id) == (0, 0)


def test_independent_replay_still_fuses():
    mem = Memory.sqlite(":memory:", agent_id="t", namespace="d")
    p = _proposal()
    # expected comes from a matched historical success, not the recipe
    audit(p, [ReplayCase(case_id="hist::1", inputs={"a": 1},
                         expected={"b": 1}),
              ReplayCase(case_id="hist::2", inputs={"a": 2},
                         expected={"WRONG": 2})],
          memory=mem)   # self_certified defaults to False
    assert replay_counts_for(mem, p.id) == (1, 1)


def test_heal_path_posterior_excludes_self_replay(monkeypatch):
    """End to end: after a heal, the standing proposal's verdict must show
    zero replay wins in its prior -- the self-check is a citation, not
    evidence."""
    def fake_build_default_healer(model_id=None, **_):
        def healer(tool_name, args, error):
            return graxella.TransformRecipe(field_map={"order_id": "order_ref"})
        return healer
    monkeypatch.setattr("graxella.healing.dspy_healer.build_default_healer",
                        fake_build_default_healer)

    grx = graxella.Session("t", domain="ship", model_id="m",
                           workdir="ephemeral")

    @grx.tool(name="ship_status", fallback=lambda a: f"ok {a['order_ref']}")
    def ship(order_id: str) -> str:
        """ship"""
        raise TypeError("unexpected keyword argument 'order_id'; schema "
                        "deprecated - use 'order_ref' instead")

    ship.invoke({"order_id": "1"})
    prop = grx.interceptors["ship_status"].standing_proposal()
    grx.gate.refresh()
    verdict = grx.gate.evaluate(prop)
    assert verdict.prior.replay_wins == 0, (
        f"self-certified replay leaked into posterior: {verdict.prior}")
    # ...but the report is still on the ledger for the reviewer, labeled:
    import json
    rows = grx.memory.beliefs(subject=prop.id, predicate="paired_replay")
    assert rows and json.loads(rows[-1]["statement"]).get("self_certified") is True
