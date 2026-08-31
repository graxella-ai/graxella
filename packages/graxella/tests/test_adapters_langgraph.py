"""Adapter contract tests against the REAL langgraph (task 0C-2).

The riskiest code in graxella is the duck-typing that reaches into
compiled-graph internals (``nodes["tools"].bound.tools_by_name``). These
tests pin that contract: if a langgraph release changes the shape, this
file goes red before production routing quality quietly degrades.

Migrated (2026-08, langchain 1.x): ``create_react_agent`` was deprecated
since LangGraph 1.0 and removed in 2.0 — these tests now bind agents via
``langchain.agents.create_agent``. The duck-typed shape contract
(``.nodes`` + ``.invoke`` + ``.name``) holds identically for both.
"""
from __future__ import annotations

import pytest
from mnema.adapters.embedder.tfidf import TfidfEmbedder

import graxella
from graxella.beliefs import Memory
from graxella.society.adapter import (
    _extract_langgraph_tools,
    _langgraph_agent_info,
    _looks_like_langgraph_agent,
    _usage_from_messages,
)

langgraph = pytest.importorskip("langgraph")
from langchain.agents import create_agent  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.tools import tool  # noqa: E402


class FakeToolChat(BaseChatModel):
    """Minimal chat model: answers immediately, never calls tools, and
    stamps usage_metadata so the token-capture path is exercised."""

    @property
    def _llm_type(self) -> str:
        return "fake-tool-chat"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        msg = AIMessage(
            content="refund approved for order",
            usage_metadata={"input_tokens": 7, "output_tokens": 3,
                            "total_tokens": 10},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])


@tool
def check_order(order_id: str) -> str:
    """look up an order and decide refund eligibility for billing complaints"""
    return f"order {order_id} eligible"


@pytest.fixture()
def react_agent():
    return create_agent(FakeToolChat(), [check_order], name="triage")


# -- shape contract ----------------------------------------------------------

def test_compiled_graph_is_detected(react_agent):
    assert _looks_like_langgraph_agent(react_agent)
    assert not _looks_like_langgraph_agent(lambda x: x)
    assert not _looks_like_langgraph_agent(object())


def test_tool_extraction_contract(react_agent):
    """THE canary: reaches into nodes['tools'].bound.tools_by_name."""
    tools = _extract_langgraph_tools(react_agent)
    assert len(tools) == 1
    assert tools[0].name == "check_order"


def test_agent_info_derives_skills_from_tool_descriptions(react_agent):
    name, tools, skills = _langgraph_agent_info(react_agent)
    assert name == "triage"
    assert len(tools) == 1
    assert any("refund eligibility" in s for s in skills)


def test_usage_metadata_summation():
    msgs = [AIMessage(content="a", usage_metadata={
                "input_tokens": 5, "output_tokens": 2, "total_tokens": 7}),
            AIMessage(content="b", usage_metadata={
                "input_tokens": 4, "output_tokens": 1, "total_tokens": 5})]
    assert _usage_from_messages(msgs) == {"input_tokens": 9, "output_tokens": 3}
    assert _usage_from_messages([AIMessage(content="no-usage")]) is None


# -- end to end through the mesh --------------------------------------------

def test_graxella_agent_llm_path_through_mesh(tmp_path):
    """graxella.Agent with llm+tools compiles to a react loop (Path B)."""
    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="t",
                           namespace="refunds", embedder=TfidfEmbedder())
    triage = graxella.Agent(
        role="Refund Triage Officer",
        goal="decide refund eligibility for billing complaints",
        backstory="You are a senior support agent.",
        tools=[check_order],
        llm=FakeToolChat(),
    )
    app = graxella.mesh([triage], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"), domain="refunds")
    result, aid = app.route("billing refund eligibility order 3")
    assert result.chosen_agent == "refund_triage_officer"
    assert memory.outcomes_for(aid)[0].ok is True


def test_graxella_agent_tools_only_and_echo_paths(tmp_path):
    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="t",
                           embedder=TfidfEmbedder())
    stub = graxella.Agent(role="Order Checker",
                          goal="look up billing orders for refunds",
                          tools=[check_order])            # tools, no llm
    echo = graxella.Agent(role="Fallback Echo",
                          goal="echo anything about shipping labels")
    app = graxella.mesh([stub, echo], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"))
    r1, _ = app.route("billing refund order lookup 55")
    assert r1.chosen_agent == "order_checker"
    r2, _ = app.route("shipping labels question")
    assert r2.chosen_agent == "fallback_echo"
    assert "received" in r2.response


def test_react_agent_routes_and_records_through_mesh(tmp_path):
    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="t",
                           namespace="refunds", embedder=TfidfEmbedder())
    agent = create_agent(FakeToolChat(), [check_order], name="triage")
    app = graxella.mesh([agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"),
                        domain="refunds", model_id="fake-tool-chat")
    result, aid = app.route("billing refund eligibility for order 12")
    assert result.chosen_agent == "triage"
    assert "refund approved" in result.response
    rec = memory.outcomes_for(aid)[0]
    assert rec.ok is True
    assert rec.model_id == "fake-tool-chat"
    # Token capture flowed from AIMessage.usage_metadata to the ledger.
    assert rec.tokens_in == 7 and rec.tokens_out == 3
