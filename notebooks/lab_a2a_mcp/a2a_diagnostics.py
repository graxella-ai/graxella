"""Diagnostics specialist: a LangGraph ReAct agent with read-only MCP ops tools,
served over A2A on port 9101."""
import asyncio

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from a2a.types import AgentSkill
from a2a_common import build_agent_card, serve

MODEL = "qwen2.5:7b"
MCP_URL = "http://127.0.0.1:8901/mcp"
PORT = 9101

PROMPT = """You are a production diagnostics specialist.
Given an incident description, find the ROOT CAUSE, not just symptoms:
1. Pull metrics for the affected service, then walk its dependency map and pull
   metrics for each dependency that looks implicated.
2. Check recent deploys for any service whose metrics look abnormal.
3. Distinguish real causes from red herrings (a metric can be bad but stable/unrelated).
Answer with: root-cause service, the offending change if any, the causal chain,
and what you ruled out. Be concrete and cite the numbers you saw."""


async def build_graph():
    client = MultiServerMCPClient({"ops": {"transport": "streamable_http", "url": MCP_URL}})
    tools = await client.get_tools()
    wanted = {"get_service_metrics", "get_dependency_map", "get_recent_deploys"}
    tools = [t for t in tools if t.name in wanted]
    llm = ChatOllama(model=MODEL, temperature=0)
    return create_react_agent(llm, tools, prompt=PROMPT)


if __name__ == "__main__":
    graph = asyncio.run(build_graph())
    card = build_agent_card(
        name="diagnostics-agent",
        description="Finds the root cause of production incidents using live metrics, dependency maps and deploy history.",
        url=f"http://127.0.0.1:{PORT}",
        skills=[AgentSkill(
            id="root_cause_analysis", name="Root cause analysis",
            description="Traces an incident through service dependencies to the causing change.",
            tags=["diagnostics", "observability"],
            examples=["Why is checkout-api slow since 09:40 UTC?"],
        )],
    )
    print(f"diagnostics-agent up on :{PORT} with tools: metrics, deps, deploys")
    serve(graph, card, PORT)
