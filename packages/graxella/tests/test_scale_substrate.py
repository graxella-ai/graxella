"""Tasks 3-5/3-6 — A2A card interop, namespace isolation, topology."""
from __future__ import annotations

import pytest

import graxella
from agent2society.a2a import from_a2a_dict, to_a2a_dict
from graxella.api.topology import render_html, topology_data
from graxella.beliefs import Memory


def billing_agent(payload):
    """decide refund eligibility for billing complaints and orders"""
    return {"result": f"ok {payload}",
            "tool_calls": [{"name": "order_lookup", "args": {}}]}


# -- 3-5: A2A cards -----------------------------------------------------------

def test_a2a_card_round_trip(tmp_path):
    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="a2a")
    app = graxella.mesh([billing_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"), recall=False)
    graph_agents = app.society._mesh._state.graph.agents()
    card = graph_agents[0].card
    wire = to_a2a_dict(card)
    assert wire["protocolVersion"] == "1.0"
    assert wire["name"] == "billing_agent"
    assert wire["skills"][0]["tags"]            # routable tags on the wire

    back = from_a2a_dict(wire)                  # another runtime's card
    assert back.name == card.name
    assert [s.id for s in back.skills] == [s.id for s in card.skills]


def test_remote_a2a_card_joins_the_mesh(tmp_path):
    """A card published by ANY A2A runtime registers like a local agent."""
    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="a2a2")
    app = graxella.mesh([billing_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"), recall=False)
    remote = {
        "protocolVersion": "1.0",
        "name": "remote_shipper",
        "url": "https://agents.example.com/shipper",
        "description": "external shipping agent",
        "skills": [{"id": "ship", "name": "shipping labels",
                    "description": "create shipping labels for orders",
                    "tags": ["shipping", "labels", "orders"]}],
    }
    app.society._mesh.add(from_a2a_dict(remote))
    assert "remote_shipper" in app.society.agents()


# -- 3-5: namespace isolation -------------------------------------------------

def test_namespaces_never_leak(tmp_path):
    db = str(tmp_path / "shared.db")
    tenant_a = Memory.sqlite(db, agent_id="t", namespace="tenant_a")
    tenant_b = Memory.sqlite(db, agent_id="t", namespace="tenant_b")
    aid = tenant_a.record_decision(decision_type="delegate", task="secret",
                                   chosen="x", domain="tenant_a")
    tenant_a.record_outcome(decision_id=aid, ok=True, domain="tenant_a")
    assert tenant_a.outcome_stats()["total"]["count"] == 1
    assert tenant_b.outcome_stats()["total"]["count"] == 0     # isolated
    assert tenant_b.beliefs(predicate="decision") == []


# -- 3-6: topology ------------------------------------------------------------

def test_topology_data_and_html(tmp_path):
    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="topo",
                           namespace="refunds")
    app = graxella.mesh([billing_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"),
                        domain="refunds", recall=False)
    app.route("billing refund order 1")
    app.route("billing refund order 2")

    data = topology_data(app)
    agent = next(n for n in data["nodes"] if n["id"] == "billing_agent")
    assert agent["routes"] == 2 and agent["ok_rate"] == 1.0
    assert data["edges"][0]["weight"] == 2

    html = render_html(data)
    assert "billing_agent" in html and "<canvas" in html
    assert "cdn" not in html.lower()            # self-contained, no CDN
