"""`graxella show` -- the governance ledger UI. Endpoint contract tests.

Single-process: seed and serve from the same Session (the CLI's
cross-process path additionally runs _ledger_identity, covered below).
"""
from __future__ import annotations

import graxella
from graxella.api.show import _ledger_identity, build_show_app

pytest_plugins: list[str] = []


def _seeded_session():
    grx = graxella.Session("show-t", domain="support", workdir="ephemeral")

    @grx.healer
    def h(t, a, e):
        return graxella.TransformRecipe(field_map={"order_id": "order_ref"})

    @grx.tool(name="ship", fallback=lambda a: f"ok {a['order_ref']}")
    def ship(order_id: str) -> str:
        """ship"""
        raise TypeError("unexpected keyword argument 'order_id'; schema "
                        "deprecated - use 'order_ref'")

    ship.invoke({"order_id": "1234"})
    d = grx.memory.record_decision(decision_type="delegate", task="refund",
                                   chosen="refunds", domain="support")
    grx.memory.record_outcome(decision_id=d, ok=True, kind="delegate",
                              chosen="refunds", domain="support",
                              session_id="s0")
    grx.memory.record_touch(d, "order:1234", role="entity",
                            detail={"by": "refunds"})
    return grx


def test_show_endpoints_and_approve():
    from fastapi.testclient import TestClient
    grx = _seeded_session()
    c = TestClient(build_show_app(grx))

    assert c.get("/").status_code == 200
    o = c.get("/api/overview").json()
    assert o["outcomes"] == 2 and o["heals"] == 1 and o["pending"] == 1
    assert o["rules_active"] == 0 and o["rules_demoted"] == 0

    p = c.get("/api/pending").json()
    assert len(p) == 1 and "posterior" in p[0]["why"]

    assert len(c.get("/api/verdicts").json()) >= 1
    assert len(c.get("/api/decisions").json()) == 2
    t = c.get("/api/touching", params={"target": "order:1234"}).json()
    assert len(t) == 1 and t[0]["role"] == "entity"

    r = c.post("/api/decide", json={"proposal_id": p[0]["proposal_id"],
                                    "action": "approve",
                                    "by": "operator:test"}).json()
    assert r["ok"] and r["promoted"]        # same-process: promotes now
    assert c.get("/api/overview").json()["pending"] == 0
    assert len(grx.rulebook.all_rules()) == 1

    # the rule shows up in the rulebook view, active
    rules = c.get("/api/rules").json()
    assert len(rules) == 1 and rules[0]["status"] == "active"
    assert c.get("/api/overview").json()["rules_active"] == 1


def test_show_reflects_demotion():
    from fastapi.testclient import TestClient
    grx = _seeded_session()
    c = TestClient(build_show_app(grx))
    pid = c.get("/api/pending").json()[0]["proposal_id"]
    c.post("/api/decide", json={"proposal_id": pid, "action": "approve",
                                "by": "operator:test"})
    rule_id = grx.rulebook.all_rules()[0].id

    # simulate the promoted rule failing repeatedly, then reconcile
    for _ in range(4):
        aid = grx.memory.record_decision(decision_type="tool", task="call",
                                         chosen="ship", domain="support")
        grx.memory.record_touch(aid, rule_id, role="rule_use",
                                detail={"ok": False})
    grx.reconcile()

    o = c.get("/api/overview").json()
    assert o["rules_active"] == 0 and o["rules_demoted"] == 1
    rules = c.get("/api/rules").json()
    demoted = next(r for r in rules if r["id"] == rule_id)
    assert demoted["status"] == "rolled_back"
    assert "posterior" in demoted["demoted_reason"]


def test_ledger_identity_detection():
    grx = _seeded_session()
    ns, agent = _ledger_identity(grx.workdir / "mnema.db")
    assert ns == "support" and agent == "show-t"
