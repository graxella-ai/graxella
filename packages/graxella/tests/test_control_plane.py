"""Task 3-1 — the control-plane service, and the availability contract."""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import graxella  # noqa: E402
from graxella.api.control_plane import create_app  # noqa: E402
from graxella.beliefs import Memory  # noqa: E402
from graxella.gate.evidence import EvidenceGate, GateDecision  # noqa: E402
from graxella.gate.spec import ArtifactKind, Proposal, TargetScope  # noqa: E402


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "m.db"), agent_id="cp",
                         namespace="refunds")


@pytest.fixture()
def client(memory):
    return TestClient(create_app(memory))


def proposal():
    return Proposal(id="prop_cp_01", kind=ArtifactKind.TOOL_BINDING,
                    target=TargetScope(domain="refunds", tool="t"),
                    payload={}, origin="miner:t")


def test_health_and_stats(client, memory):
    assert client.get("/health").json()["status"] == "ok"
    aid = memory.record_decision(decision_type="delegate", task="t",
                                 chosen="a::s", domain="refunds")
    memory.record_outcome(decision_id=aid, ok=True, tokens_in=9,
                          domain="refunds")
    stats = client.get("/stats").json()
    assert stats["total"]["count"] == 1 and stats["total"]["tokens_in"] == 9


def test_review_loop_over_http(client, memory):
    """The 1-8 review loop, but through the service."""
    EvidenceGate(memory).decide(proposal())
    pend = client.get("/gate/pending").json()
    assert pend[0]["proposal_id"] == "prop_cp_01"

    why = client.get("/gate/why/prop_cp_01").json()["why"]
    assert "NEEDS_HUMAN" in why

    r = client.post("/gate/approve", json={"proposal_id": "prop_cp_01",
                                           "by": "sridhar", "note": "ok"})
    assert r.status_code == 200
    assert client.get("/gate/pending").json() == []

    v, _ = EvidenceGate(memory).decide(proposal())
    assert v.decision is GateDecision.AUTO_APPROVE


def test_ingest_applies_wal_batches(client, memory):
    """A remote data plane pushes buffered writes; ids are preserved."""
    batch = [{"statement": "remote decision", "assertion_id": "asr_remote1",
              "subject": "decision::delegate::x", "predicate": "decision",
              "object": "x", "source_id": "orchestrator"}]
    r = client.post("/ingest", json=batch)
    assert r.json()["applied"] == 1
    assert r.json()["assertion_ids"] == ["asr_remote1"]
    rows = memory.beliefs(predicate="decision")
    assert rows[0]["id"] == "asr_remote1"


def test_dispatch_survives_without_the_control_plane(tmp_path):
    """The availability contract: no control plane anywhere, dispatch and
    buffered evidence still work — sync happens whenever it returns."""
    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="cp2",
                           namespace="refunds", buffered=True)

    def billing_agent(payload):
        """decide refund eligibility for billing complaints"""
        return {"result": f"ok {payload}"}

    app = graxella.mesh([billing_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"),
                        domain="refunds", recall=False)
    _, aid = app.route("billing refund order 1")     # no service running
    assert memory.outcomes_for(aid)[0].ok is True
