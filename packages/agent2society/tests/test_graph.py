from __future__ import annotations

import pytest

import agent2society as r2r


def test_add_agent_and_lookup(research_card):
    g = r2r.CapabilityGraph()
    g.add_agent(r2r.parse_card(research_card))
    node = g.require("research-agent")
    assert node.skill_ids == ["web_research"]
    assert g.get("nope") is None


def test_boundary_merges_additively(research_card):
    g = r2r.CapabilityGraph()
    g.add_agent(r2r.parse_card(research_card))
    g.set_boundary("research-agent", deny=["financial-data"])
    g.set_boundary("research-agent", deny=["pii"], allow=["public"])
    b = g.require("research-agent").boundary
    assert "financial-data" in b.deny
    assert "pii" in b.deny
    assert "public" in b.allow


def test_dependency_requires_known_agents(research_card, writer_card):
    g = r2r.CapabilityGraph()
    g.add_agent(r2r.parse_card(research_card))
    g.add_agent(r2r.parse_card(writer_card))
    g.add_dependency("writer-agent", "research-agent")
    edges = g.edges()
    assert ("writer-agent", "depends_on", "research-agent") in edges
    assert ("research-agent", "declares", "web_research") in edges


def test_dependency_rejects_unknown(research_card):
    g = r2r.CapabilityGraph()
    g.add_agent(r2r.parse_card(research_card))
    with pytest.raises(KeyError):
        g.add_dependency("writer-agent", "research-agent")
