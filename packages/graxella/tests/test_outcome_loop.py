"""Workstream 0A — the closed evidence loop.

0A-1: typed SPO outcomes, corrected epistemics, provenance linking.
0A-2: route() auto-records decision + outcome; error path included.
0A-3: token accounting computed from the ledger alone.
"""
from __future__ import annotations

import json

import pytest

import graxella
from graxella.beliefs import Memory
from graxella.beliefs.records import (OBSERVED_CONFIDENCE, OutcomeRecord,
                                      is_outcome_statement)


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "mnema.db"), agent_id="t-agent",
                         namespace="refunds")


def _mesh(tmp_path, memory, agents):
    return graxella.mesh(
        agents, memory=memory,
        store_path=str(tmp_path / "routes.jsonl"),
        domain="refunds", model_id="stub-llm-1",
    )


def check_order(payload):
    """look up an order and decide refund eligibility for billing complaints"""
    return {"result": f"order checked: {payload}",
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def draft_email(payload):
    """write a friendly response email to the customer"""
    return {"result": f"email drafted: {payload}"}


# -- 0A-1: typed outcomes ---------------------------------------------------

def test_outcome_is_typed_spo_not_prose(memory):
    aid = memory.record_decision(decision_type="delegate", task="refund order 12",
                                 chosen="triage::check", domain="refunds")
    oid = memory.record_outcome(decision_id=aid, ok=True, latency_ms=42.0,
                                tokens_in=100, tokens_out=20, chosen="triage::check",
                                domain="refunds", model_id="qwen2.5:3b")
    rows = memory._client.beliefs(subject=aid, predicate="outcome")
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == oid
    assert row["object"] == "ok"
    # Statement is machine-parseable JSON, never prose.
    assert is_outcome_statement(row["statement"])
    rec = OutcomeRecord.from_statement(row["statement"])
    assert rec.decision_id == aid
    assert rec.tokens_in == 100 and rec.tokens_out == 20
    assert rec.model_id == "qwen2.5:3b"
    assert rec.domain == "refunds"


def test_observed_failure_is_high_confidence(memory):
    aid = memory.record_decision(decision_type="delegate", task="t",
                                 chosen="a::s")
    memory.record_outcome(decision_id=aid, ok=False, err="boom",
                          err_class="RuntimeError")
    row = memory._client.beliefs(subject=aid, predicate="outcome")[0]
    assert row["object"] == "fail"
    # The old 0.5-on-failure epistemics are dead: watching it fail is
    # near-certain knowledge that it failed.
    assert row["confidence"] == OBSERVED_CONFIDENCE


def test_outcome_provenance_links_decision(memory):
    aid = memory.record_decision(decision_type="delegate", task="t", chosen="a::s")
    memory.record_outcome(decision_id=aid, ok=True)
    row = memory._client.beliefs(subject=aid, predicate="outcome")[0]
    assert aid in row["derived_from"]


def test_outcomes_for_helper(memory):
    aid = memory.record_decision(decision_type="delegate", task="t", chosen="a::s")
    memory.record_outcome(decision_id=aid, ok=True, score=0.9)
    recs = memory.outcomes_for(aid)
    assert len(recs) == 1 and recs[0].completion == 0.9


# -- 0A-2: route() auto-records ---------------------------------------------

def test_route_records_decision_and_outcome(tmp_path, memory):
    app = _mesh(tmp_path, memory, [check_order, draft_email])
    result, aid = app.route("customer complains about billing on order 12")
    assert result.chosen_agent == "check_order"
    outcomes = memory.outcomes_for(aid)
    assert len(outcomes) == 1
    rec = outcomes[0]
    assert rec.ok is True
    assert rec.kind == "delegate"
    assert rec.domain == "refunds"
    assert rec.model_id == "stub-llm-1"
    assert rec.latency_ms is not None and rec.latency_ms >= 0
    assert rec.chosen == "check_order::" + str(rec.chosen).split("::")[1]


def test_route_captures_stub_usage_tokens(tmp_path, memory):
    app = _mesh(tmp_path, memory, [check_order, draft_email])
    _, aid = app.route("billing refund eligibility for order 99")
    rec = memory.outcomes_for(aid)[0]
    assert rec.tokens_in == 10 and rec.tokens_out == 5


def test_no_unverified_path(tmp_path, memory):
    """Every route() produces exactly one decision and one outcome."""
    app = _mesh(tmp_path, memory, [check_order, draft_email])
    for task in ("billing question", "draft a reply email", "order lookup"):
        app.route(task)
    decisions = memory._client.beliefs(predicate="decision")
    outcomes = memory._client.beliefs(predicate="outcome")
    assert len(decisions) == 3
    assert len(outcomes) == 3
    decision_ids = {d["id"] for d in decisions}
    assert all(o["subject"] in decision_ids for o in outcomes)


def test_failed_dispatch_records_fail_outcome(tmp_path, memory):
    def exploding_agent(payload):
        """handle every refund and billing task with confidence"""
        raise RuntimeError("upstream deprecated")

    app = _mesh(tmp_path, memory, [exploding_agent])
    try:
        app.route("refund billing task")
    except Exception:
        pass  # raising is acceptable — but the ledger must know either way
    outcomes = memory._client.beliefs(predicate="outcome")
    assert len(outcomes) == 1
    rec = OutcomeRecord.from_statement(outcomes[0]["statement"])
    assert rec.ok is False
    assert rec.err is not None


# -- 0A-3: token accounting from the ledger ---------------------------------

def test_outcome_stats_from_ledger_alone(tmp_path, memory):
    app = _mesh(tmp_path, memory, [check_order, draft_email])
    app.route("billing refund order 1")
    app.route("billing refund order 2")
    app.route("draft the apology email")
    stats = memory.outcome_stats()
    total = stats["total"]
    assert total["count"] == 3
    assert total["ok"] == 3 and total["ok_rate"] == 1.0
    # Two check_order dispatches carried stub usage; the email one none.
    assert total["tokens_in"] == 20 and total["tokens_out"] == 10
    assert total["avg_latency_ms"] is not None
    assert "refunds" in stats["by_domain"]
    assert stats["by_domain"]["refunds"]["count"] == 3


def test_outcome_stats_domain_filter(memory):
    a1 = memory.record_decision(decision_type="delegate", task="t1", chosen="x",
                                domain="refunds")
    memory.record_outcome(decision_id=a1, ok=True, domain="refunds", tokens_in=7)
    a2 = memory.record_decision(decision_type="delegate", task="t2", chosen="y",
                                domain="shipping")
    memory.record_outcome(decision_id=a2, ok=False, domain="shipping")
    only = memory.outcome_stats(domain="shipping")
    assert only["total"]["count"] == 1 and only["total"]["ok"] == 0
    both = memory.outcome_stats()
    assert both["total"]["count"] == 2
    assert set(both["by_domain"]) == {"refunds", "shipping"}


def test_stats_are_json_safe(memory):
    aid = memory.record_decision(decision_type="delegate", task="t", chosen="a")
    memory.record_outcome(decision_id=aid, ok=True)
    json.dumps(memory.outcome_stats())  # must not raise
