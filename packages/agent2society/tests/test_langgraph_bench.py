"""Tests for the real-LangGraph head-to-head harness.

These tests are skipped if `langgraph` isn't installed (kept optional).
"""
from __future__ import annotations

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")

from agent2society.bench import (
    LangGraphSupervisor,
    MultiStepBench,
    Scenario,
    default_agent_runner,
    default_scenarios,
)
from agent2society.bench.tasks import default_mesh_cards


def _mini_scenario():
    return Scenario(
        name="mini",
        objective="Research X then draft a memo about it.",
        expected_steps=[
            ("research-agent", "Research churn drivers from the web"),
            ("writer-agent", "Draft an executive memo on the research findings"),
        ],
    )


def test_langgraph_supervisor_runs_and_records_per_call_tokens():
    cards = default_mesh_cards()
    sc = _mini_scenario()
    decisions = [a for a, _ in sc.expected_steps] + ["FINISH"]
    sup = LangGraphSupervisor(
        cards,
        decisions=decisions,
        agent_runner=default_agent_runner,
    )
    run = sup.run(sc.objective)
    # 3 supervisor LLM calls: route1, route2, FINISH.
    assert run.supervisor_calls == 3
    assert run.coordination_prompt_tokens > 0
    assert run.coordination_completion_tokens > 0
    # The supervisor must have driven the agents in order.
    assert run.steps_taken == ["research-agent", "writer-agent"]


def test_multistep_bench_shows_agent2society_token_advantage():
    bench = MultiStepBench(
        cards=default_mesh_cards(),
        scenarios=default_scenarios(),
    )
    result = bench.run()

    sup_total = result.supervisor_total_tokens()
    our_total = result.agent2society_total_tokens()

    # The supervisor pays at minimum one realistic LLM round-trip per step
    # plus one FINISH per scenario, all carrying the full agent roster in
    # the system prompt. agent2society pays one short embedding per sub-task.
    # The gap is at least an order of magnitude in any honest setup.
    assert sup_total > our_total * 10, (
        f"expected at least a 10x reduction; "
        f"sup={sup_total}, ours={our_total}"
    )

    # Both methods must run every step end-to-end without errors.
    for s in result.scenarios:
        assert s.langgraph.error is None, s.langgraph.error
        assert s.agent2society.error is None, s.agent2society.error

    # LangGraph's fake supervisor is pre-programmed to route correctly so
    # it should hit every step. agent2society may miss lexically-ambiguous ones
    # under the default TF-IDF; we assert the parity claim instead: it
    # gets the *easy* steps and the harness reports the misses honestly.
    assert result.supervisor_steps_correct() == result.total_steps()
    # Sanity floor: agent2society should clear at least 70% of steps on the
    # default suite using only the TF-IDF default. (Real embeddings close
    # the gap; this is the lower bound we're willing to ship.)
    assert (
        result.agent2society_steps_correct() / result.total_steps() >= 0.70
    ), (
        f"agent2society only got {result.agent2society_steps_correct()}/"
        f"{result.total_steps()} — lexical routing regressed"
    )

    rendered = result.render()
    assert "langgraph supervisor" in rendered
    assert "agent2society" in rendered
    assert "reduction:" in rendered
