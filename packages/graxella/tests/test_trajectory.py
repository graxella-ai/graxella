"""Phase 2, tasks 2-1/2-2 — the bounded multi-hop trajectory runtime.
FM-1.3 (step repetition) and FM-1.5 (termination) containment, typed
audited re-routes, and the trajectory as a ledger object."""
from __future__ import annotations

import json

import pytest

import graxella
from graxella.beliefs import Memory
from graxella.trajectory import TrajectoryBudget


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "m.db"), agent_id="trj",
                         namespace="refunds")


def _mesh(tmp_path, memory, agents, **kw):
    return graxella.mesh(agents, memory=memory,
                         store_path=str(tmp_path / "r.jsonl"),
                         domain="refunds", recall=False, **kw)


def triage(payload):
    """decide refund eligibility for billing complaints and orders"""
    return {"result": "eligible. HANDOFF: draft_email :: write a friendly "
                      f"apology email response to the customer about {payload}"}


def draft_email(payload):
    """write a friendly apology email response to the customer"""
    return {"result": f"Dear customer, {payload}. Sincerely."}


def test_single_hop_completes(tmp_path, memory):
    app = _mesh(tmp_path, memory, [draft_email])
    t = app.run_trajectory("write the apology email")
    assert t.status == "completed" and t.n_hops == 1
    assert not t.escalated
    # The chain is a ledger object citing its hop decisions.
    row = memory.beliefs(subject=t.id, predicate="trajectory")[0]
    assert row["object"] == "completed"
    assert row["derived_from"] == [t.hops[0].decision_id]


def test_typed_handoff_is_an_audited_second_hop(tmp_path, memory):
    app = _mesh(tmp_path, memory, [triage, draft_email])
    t = app.run_trajectory("billing refund order 12 arrived damaged")
    assert t.status == "completed"
    assert [h.agent for h in t.hops] == ["triage", "draft_email"]
    assert "Dear customer" in t.final_response
    # BOTH hops carry full decisions + typed outcomes.
    for h in t.hops:
        assert memory.outcomes_for(h.decision_id)[0].ok is True
    data = json.loads(memory.beliefs(subject=t.id,
                                     predicate="trajectory")[0]["statement"])
    assert [h["agent"] for h in data["hops"]] == ["triage", "draft_email"]


def test_loop_is_detected_and_escalated(tmp_path, memory):
    def ping(payload):
        """handle billing refunds for orders"""
        return {"result": "HANDOFF: pong :: same billing task"}

    def pong(payload):
        """verify billing refunds for orders"""
        return {"result": "HANDOFF: ping :: same billing task"}

    app = _mesh(tmp_path, memory, [ping, pong])
    t = app.run_trajectory("billing refund order 9",
                           budget=TrajectoryBudget(max_hops=10))
    assert t.status == "loop_detected"      # FM-1.3: stopped, not burned
    assert t.escalated
    assert t.n_hops < 10
    assert memory.signals(kind="trajectory_escalation")[0]["status"] == "loop_detected"
    assert len(app.tracer.events(event_type="trajectory.escalated")) == 1


def _churn_pair(usage=None):
    """Two agents that chain work between each other endlessly, with
    varying content so loop detection doesn't fire first. (Self-handoffs
    now complete immediately — a real-LLM lesson from showcase 08.)"""
    counter = {"n": 0}

    def _result(target):
        counter["n"] += 1
        out = {"result": f"step {counter['n']} done. HANDOFF: {target} :: "
                         f"continue the billing refund order step "
                         f"{counter['n'] + 1}"}
        if usage:
            out["usage"] = dict(usage)
        return out

    def churner_a(payload):
        """process billing refunds for orders step by step"""
        return _result("churner_b")

    def churner_b(payload):
        """verify billing refund order steps and continue processing"""
        return _result("churner_a")

    return churner_a, churner_b


def test_hop_budget_contains_runaway_chains(tmp_path, memory):
    app = _mesh(tmp_path, memory, list(_churn_pair()))
    t = app.run_trajectory("billing refund order 1",
                           budget=TrajectoryBudget(max_hops=3))
    assert t.status == "budget_exhausted"   # FM-1.5: contained, escalated
    assert t.n_hops == 3
    assert t.escalated
    assert memory.signals(kind="trajectory_escalation")[0]["hops"] == 3


def test_token_budget(tmp_path, memory):
    app = _mesh(tmp_path, memory,
                list(_churn_pair(usage={"input_tokens": 60,
                                        "output_tokens": 40})))
    t = app.run_trajectory("billing refund order 2",
                           budget=TrajectoryBudget(max_hops=50, max_tokens=150))
    assert t.status == "budget_exhausted"
    assert t.n_hops == 2                    # 100 tokens/hop; capped at 150


def test_self_handoff_completes_instead_of_spinning(tmp_path, memory):
    def solo(payload):
        """process billing refunds for orders step by step"""
        return {"result": "done my part. HANDOFF: solo :: keep going"}

    app = _mesh(tmp_path, memory, [solo])
    t = app.run_trajectory("billing refund order 9",
                           budget=TrajectoryBudget(max_hops=5))
    assert t.status == "completed" and t.n_hops == 1
    events = app.tracer.events(event_type="trajectory.self_handoff_ignored")
    assert len(events) == 1


def test_handoff_to_unknown_peer_finishes_loudly(tmp_path, memory):
    def dreamer(payload):
        """handle billing refunds for orders"""
        return {"result": "done. HANDOFF: nonexistent_agent :: whatever"}

    app = _mesh(tmp_path, memory, [dreamer])
    t = app.run_trajectory("billing refund order 3")
    assert t.status == "completed" and t.n_hops == 1
    assert len(app.tracer.events(event_type="degradation.handoff_unknown")) == 1
