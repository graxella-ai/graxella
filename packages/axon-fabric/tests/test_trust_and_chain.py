"""Tasks 2-6 (tool trust scores) and 2-7 (chain-healing miner)."""
from __future__ import annotations

import pytest

import graxella
from axon_fabric.trust import preferred, tool_trust
from graxella.agenda.chain import ChainMiner
from graxella.beliefs import Memory
from graxella.gate.spec import ArtifactKind
from graxella.trajectory import TrajectoryBudget


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "m.db"), agent_id="tc",
                         namespace="weather")


def seed_tool(memory, tool, *, ok, fail):
    for i in range(ok + fail):
        aid = memory.record_decision(decision_type="tool", task=f"call {i}",
                                     chosen=tool, domain="weather")
        memory.record_outcome(decision_id=aid, ok=i < ok, kind="tool",
                              chosen=tool, domain="weather",
                              err_class=None if i < ok else "drift",
                              session_id=f"s{i}")


# -- 2-6 ----------------------------------------------------------------------

def test_trust_is_cited_and_ranked(memory):
    seed_tool(memory, "weather_v1", ok=2, fail=8)
    seed_tool(memory, "weather_v2", ok=9, fail=1)
    trust = tool_trust(memory, domain="weather")
    assert trust["weather_v2"].score > trust["weather_v1"].score
    assert trust["weather_v1"].last_err_class == "drift"
    assert len(trust["weather_v1"].citations) == 10   # every number cited


def test_degrading_tool_visibly_drops(memory):
    seed_tool(memory, "api", ok=5, fail=0)
    before = tool_trust(memory)["api"].score
    seed_tool(memory, "api", ok=0, fail=5)
    after = tool_trust(memory)["api"].score
    assert after < before


def test_preferred_failover_order(memory):
    seed_tool(memory, "old_api", ok=1, fail=9)
    seed_tool(memory, "new_api", ok=9, fail=1)
    order = preferred(memory, ["old_api", "brand_new", "new_api"],
                      domain="weather")
    assert order[0] == "new_api"          # evidence beats novelty
    assert order[1] == "brand_new"        # novelty beats a known-bad record
    assert order[2] == "old_api"


# -- 2-7 ----------------------------------------------------------------------

def _loop_mesh(tmp_path, memory, name):
    def ping(payload):
        """handle billing refunds for orders"""
        return {"result": "HANDOFF: pong :: same billing refund task"}

    def pong(payload):
        """verify billing refunds for orders"""
        return {"result": "HANDOFF: ping :: same billing refund task"}

    return graxella.mesh([ping, pong], memory=memory,
                         store_path=str(tmp_path / f"{name}.jsonl"),
                         domain="weather", recall=False)


def test_repeated_chain_escalations_become_playbook_proposal(tmp_path, memory):
    app = _loop_mesh(tmp_path, memory, "r1")
    app.run_trajectory("billing refund order 1",
                       budget=TrajectoryBudget(max_hops=6))
    app.run_trajectory("billing refund order 2",
                       budget=TrajectoryBudget(max_hops=6))

    proposals = ChainMiner(memory).mine()
    assert len(proposals) == 1
    p = proposals[0]
    assert p.kind is ArtifactKind.PLAYBOOK
    assert p.payload["issue"] == "chain_loop_detected"
    assert p.payload["occurrences"] == 2
    assert "HANDOFF" in p.payload["delta_items"][0]
    assert len(p.evidence) == 2
    assert ChainMiner(memory).mine()[0].id == p.id     # deterministic


def test_single_escalation_below_support(tmp_path, memory):
    app = _loop_mesh(tmp_path, memory, "r2")
    app.run_trajectory("billing refund order 1",
                       budget=TrajectoryBudget(max_hops=6))
    assert ChainMiner(memory).mine() == []
