from __future__ import annotations

import pytest

import agent2society as r2r
from agent2society.conformance import check


def _graph(cards):
    g = r2r.CapabilityGraph()
    for c in cards:
        g.add_agent(r2r.parse_card(c))
    return g


def test_allows_when_skill_declared(writer_card):
    g = _graph([writer_card])
    res = check(g, agent="writer-agent", skill_id="exec_memo", task="draft memo")
    assert res.ok


def test_blocks_undeclared_skill(writer_card):
    g = _graph([writer_card])
    res = check(g, agent="writer-agent", skill_id="financial_analysis", task="x")
    assert not res.ok
    assert "does not declare skill" in res.reason


def test_blocks_unknown_agent(writer_card):
    g = _graph([writer_card])
    res = check(g, agent="ghost", skill_id="exec_memo", task="x")
    assert not res.ok


def test_blocks_when_task_hits_deny_term(writer_card):
    g = _graph([writer_card])
    g.set_boundary("writer-agent", deny=["financial-data"])
    res = check(
        g,
        agent="writer-agent",
        skill_id="exec_memo",
        task="Draft a memo with financial-data figures",
    )
    assert not res.ok
    assert "denied term" in res.reason


def test_blocks_when_allow_list_present_but_no_match(writer_card):
    g = _graph([writer_card])
    g.set_boundary("writer-agent", allow=["marketing"])
    res = check(
        g,
        agent="writer-agent",
        skill_id="exec_memo",
        task="Draft an exec memo about churn",
    )
    assert not res.ok


def test_mesh_run_raises_on_violation(writer_card, monkeypatch):
    m = r2r.Mesh()
    m.add(writer_card)
    m.boundary("writer-agent", deny=["financial"])
    with pytest.raises(r2r.ConformanceViolation):
        m.run("Draft a financial memo")
