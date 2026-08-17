"""Workstream 0B — case recall (the Memento pattern).

0B-1: similarity search over verified (decision, outcome) pairs.
0B-2: dispatch-time injection via the Society context slot — recall
      never touches the task string, so routing stays recall-blind.
"""
from __future__ import annotations

import pytest
from mnema.adapters.embedder.tfidf import TfidfEmbedder

import graxella
from graxella.beliefs import Memory
from graxella.beliefs.records import RECALL_MAX_CHARS, RecalledCase, render_recall_block


@pytest.fixture()
def memory(tmp_path):
    # Explicit TF-IDF embedder: deterministic and Ollama-independent.
    return Memory.sqlite(str(tmp_path / "mnema.db"), agent_id="t-agent",
                         namespace="refunds", embedder=TfidfEmbedder())


def check_order(payload):
    """look up an order and decide refund eligibility for billing complaints"""
    return {"result": f"checked {payload}"}


def draft_email(payload):
    """write a friendly response email to the customer"""
    return {"result": f"drafted {payload}"}


def _seed(memory, task, chosen, *, ok, err=None, completion=None):
    aid = memory.record_decision(decision_type="delegate", task=task,
                                 chosen=chosen, domain="refunds")
    memory.record_outcome(decision_id=aid, ok=ok, err=err, score=completion,
                          chosen=chosen, domain="refunds")
    return aid


# -- 0B-1: similarity search ------------------------------------------------

def test_similar_cases_returns_verified_pairs(memory):
    _seed(memory, "refund the billing charge on order 41", "check_order::x", ok=True)
    _seed(memory, "customer wants billing refund for order 42", "check_order::x",
          ok=False, err="tool deprecated")
    _seed(memory, "write a poem about shipping", "draft_email::y", ok=True)

    cases = memory.similar_cases("billing refund question order 43", top_k=2)
    assert 1 <= len(cases) <= 2
    assert all(isinstance(c, RecalledCase) for c in cases)
    # Both billing decisions outrank the poem.
    assert all("billing" in c.task for c in cases)
    assert any(not c.ok for c in cases)  # the failure is recalled too
    failed = next(c for c in cases if not c.ok)
    assert failed.err == "tool deprecated"


def test_decisions_without_outcomes_are_skipped(memory):
    # A decision that never got an outcome is unverified — not recallable.
    memory.record_decision(decision_type="delegate",
                           task="billing refund order 77", chosen="check_order::x",
                           domain="refunds")
    assert memory.similar_cases("billing refund order 78") == []


def test_recall_block_is_bounded_and_advisory():
    cases = [RecalledCase(similarity=0.9, task="t" * 300, chosen="a::b", ok=True)
             for _ in range(20)]
    block = render_recall_block(cases)
    assert len(block) <= RECALL_MAX_CHARS
    assert "guidance" in block
    assert render_recall_block([]) == ""


# -- 0B-2: dispatch-time injection ------------------------------------------

def _mesh(tmp_path, memory, agents, **kw):
    return graxella.mesh(agents, memory=memory,
                         store_path=str(tmp_path / "routes.jsonl"),
                         domain="refunds", **kw)


def test_recall_active_during_dispatch_only(tmp_path, memory):
    app = _mesh(tmp_path, memory, [check_order, draft_email])
    app.route("billing refund order 1")           # first: no history yet
    card = app.society._cards["check_order"]
    assert card.last_recall == ""

    app.route("billing refund order 2")           # second: recall fires
    assert "Similar past tasks" in card.last_recall
    assert "billing refund order 1" in card.last_recall
    # The slot is cleared after dispatch — nothing leaks across calls.
    assert app.society._recall_slot[0] == ""


def test_recall_never_touches_routing_scores(tmp_path, memory):
    """Same task routed with and without history must score identically —
    recall is dispatch-time context, invisible to the router."""
    app = _mesh(tmp_path, memory, [check_order, draft_email])
    r1, _ = app.route("billing refund order 9")
    r2, _ = app.route("billing refund order 9")
    assert r1.chosen_agent == r2.chosen_agent
    assert r1.score == pytest.approx(r2.score)


def test_recall_off_switch(tmp_path, memory):
    app = _mesh(tmp_path, memory, [check_order, draft_email], recall=False)
    app.route("billing refund order 1")
    app.route("billing refund order 2")
    card = app.society._cards["check_order"]
    assert card.last_recall == ""


def test_recall_reaches_llm_context_of_langgraph_agent(tmp_path, memory):
    """A fake compiled-graph agent proves the block lands as a system
    message in the LLM's actual input, not just on the card."""

    class FakeCompiledGraph:
        name = "triage"
        nodes: dict = {}
        seen_messages: list = []

        def invoke(self, state):
            self.seen_messages.append(list(state["messages"]))
            return {"messages": []}

    fake = FakeCompiledGraph()
    app = _mesh(tmp_path, memory, [fake])
    # Seed history so the second dispatch has something to recall.
    _seed(memory, "triage the billing refund for order 5", "triage::t", ok=True)

    app.route("triage agent billing refund order 6")
    msgs = fake.seen_messages[-1]
    systems = [c for role, c in msgs if role == "system"]
    assert any("Similar past tasks" in s for s in systems)
    users = [c for role, c in msgs if role == "user"]
    assert all("Similar past tasks" not in u for u in users)  # never in task


def test_recall_injection_is_traced(tmp_path, memory):
    app = _mesh(tmp_path, memory, [check_order, draft_email])
    app.route("billing refund order 1")
    app.route("billing refund order 2")
    events = app.tracer.events(source="orchestrator", event_type="recall.injected")
    assert len(events) == 1
    assert events[0].payload["cases"] >= 1
    assert events[0].payload["chars"] > 0
