"""Task 2-3 — L1 progressive disclosure: the routing shortlist's skill
summaries reach the WINNER's context, at flat cost in mesh size."""
from __future__ import annotations

import pytest

import graxella
from graxella.beliefs import Memory
from graxella.society.adapter import _L1_MAX_CHARS


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "m.db"), agent_id="disc",
                         namespace="refunds")


def billing_agent(payload):
    """decide refund eligibility for billing complaints and orders"""
    return {"result": f"handled {payload}"}


def refund_auditor(payload):
    """audit billing refunds and validate order complaints"""
    return {"result": f"audited {payload}"}


def email_agent(payload):
    """write a friendly response email to the customer"""
    return {"result": f"drafted {payload}"}


def _mesh(tmp_path, memory, agents):
    return graxella.mesh(agents, memory=memory,
                         store_path=str(tmp_path / "r.jsonl"),
                         domain="refunds", recall=False)


def test_winner_sees_runner_up_summaries(tmp_path, memory):
    app = _mesh(tmp_path, memory, [billing_agent, refund_auditor, email_agent])
    result, _ = app.route("billing refund order complaint 12")
    card = app.society._cards[result.chosen_agent]
    assert "Peers ranked most relevant" in card.last_recall
    # The runner-up (the other billing-flavored agent) is disclosed...
    other = ("refund_auditor" if result.chosen_agent == "billing_agent"
             else "billing_agent")
    assert other in card.last_recall
    # ...and the winner never lists itself.
    assert f"- {result.chosen_agent}:" not in card.last_recall


def test_flat_cost_in_mesh_size(tmp_path, memory):
    """The L1 block is bounded by top-k, not by how many agents exist."""
    def make(i):
        def a(payload):
            return {"result": "x"}
        a.__name__ = f"billing_helper_{i}"
        a.__doc__ = f"handle billing refund orders variant {i}"
        return a

    few = _mesh(tmp_path, memory, [make(i) for i in range(3)])
    r1, _ = few.route("billing refund order 1")
    small = len(few.society._cards[r1.chosen_agent].last_recall)

    memory2 = Memory.sqlite(str(tmp_path / "m2.db"), agent_id="disc2")
    many = graxella.mesh([make(i) for i in range(15)], memory=memory2,
                         store_path=str(tmp_path / "r2.jsonl"), recall=False)
    r2, _ = many.route("billing refund order 1")
    large = len(many.society._cards[r2.chosen_agent].last_recall)

    assert large <= _L1_MAX_CHARS
    assert abs(large - small) <= 120        # flat: ±one summary line


def test_single_agent_gets_no_l1(tmp_path, memory):
    app = _mesh(tmp_path, memory, [billing_agent])
    result, _ = app.route("billing refund order 1")
    assert "Peers ranked" not in app.society._cards["billing_agent"].last_recall


def test_l1_and_recall_compose(tmp_path, memory):
    app = graxella.mesh([billing_agent, refund_auditor], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"),
                        domain="refunds", recall=True)
    app.route("billing refund order 1")
    result, _ = app.route("billing refund order 2")
    ctx = app.society._cards[result.chosen_agent].last_recall
    assert "Similar past tasks" in ctx       # 0B recall block
    assert "Peers ranked" in ctx             # 2-3 L1 block