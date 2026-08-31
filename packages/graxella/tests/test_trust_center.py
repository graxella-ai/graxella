"""The trust-center UI — every differentiator served from the live session."""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
import graxella
from fastapi.testclient import TestClient


@pytest.fixture()
def grx(tmp_path):
    return graxella.Session("tc", domain="guide", model_id="m",
                            workdir=tmp_path)


def seed(grx):
    """One pass over the differentiators: observed calls (one failing),
    a drifted tool healed once, and a mesh route + trajectory."""
    @grx.tool
    def check_order(order_id: str) -> str:
        """look up an order"""
        if order_id == "bad":
            raise KeyError("not found")
        return f"order {order_id}: ok"

    @grx.healer
    def healer(tool_name, args, error):
        return graxella.TransformRecipe(field_map={"order_id": "order_ref"})

    def v2(args):
        return f"shipment {args['order_ref']}: ok"

    @grx.tool(fallback=v2)
    def get_shipping_status(order_id: str) -> str:
        """get shipping status"""
        raise RuntimeError("HTTP_410_GONE")

    check_order.invoke({"order_id": "1"})
    with pytest.raises(KeyError):
        check_order.invoke({"order_id": "bad"})
    get_shipping_status.invoke({"order_id": "1"})   # heal-once
    get_shipping_status.invoke({"order_id": "2"})   # proposed-heal reuse

    def billing(task: str) -> str:
        """handle billing refunds and payment questions"""
        return f"[billing] {task}"

    def tech_support(task: str) -> str:
        """troubleshoot device setup and technical problems"""
        return f"[tech_support] {task}"

    app = grx.mesh([billing, tech_support], recall=False)
    app.invoke("refunds question about billing on order 1")
    app.run_trajectory("handle billing refunds for order 2", max_hops=2)


def test_page_and_data_reflect_the_session(grx):
    seed(grx)
    client = TestClient(grx.api())

    for path in ("/", "/ui"):
        page = client.get(path)
        assert page.status_code == 200
        assert "graxella trust center" in page.text

    d = client.get("/ui/data").json()
    assert d["session"]["name"] == "tc"
    assert d["stats"]["total"]["count"] > 0
    assert len(d["ledger"]) > 0
    assert d["healing"]["healer_calls"] == 1
    assert d["healing"]["drifted_served"] == 2
    assert "check_order" in d["trust"]
    assert d["trust"]["check_order"]["citations"]
    assert len(d["pending"]) == 1          # the heal proposal awaits review
    assert "NEEDS_HUMAN" in d["pending"][0]["why"]
    assert d["routes"] and d["routes"][0]["decision_id"].startswith("asr_")
    assert d["trajectories"][0]["status"] == "completed"
    assert d["trajectories"][0]["hops"] == ["billing"]


def test_approve_from_the_ui_promotes_through_the_gate(grx):
    seed(grx)
    client = TestClient(grx.api())

    pid = client.get("/ui/data").json()["pending"][0]["proposal_id"]
    r = client.post("/ui/approve", json={"proposal_id": pid,
                                         "by": "operator:test", "note": "ok"})
    assert r.status_code == 200

    d = client.get("/ui/data").json()
    assert d["pending"] == []
    assert d["healing"]["rules_promoted"] == 1
    assert d["rulebook"] and "order_ref" in d["rulebook"]
    assert any(x["proposal_id"] == pid for x in d["decided"])


def test_reject_from_the_ui_is_honored(grx):
    seed(grx)
    client = TestClient(grx.api())

    pid = client.get("/ui/data").json()["pending"][0]["proposal_id"]
    client.post("/ui/reject", json={"proposal_id": pid,
                                    "by": "operator:test", "note": "no"})
    d = client.get("/ui/data").json()
    assert d["pending"] == []
    assert d["healing"]["rules_promoted"] == 0
    dec = next(x for x in d["decided"] if x["proposal_id"] == pid)
    assert dec["decision"] == "human_rejected"
