"""Remediation specialist: a LangGraph ReAct agent with runbook + ticket MCP tools,
served over A2A on port 9102."""
import asyncio

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from a2a.types import AgentSkill
from a2a_common import build_agent_card, serve

MODEL = "qwen2.5:7b"
MCP_URL = "http://127.0.0.1:8901/mcp"
PORT = 9102

PROMPT = """You are an incident remediation planner.
You receive a diagnosed root cause. Your job:
1. search_runbooks for the failure mode and pick the applicable runbook(s).
2. Produce a concrete, ordered remediation plan (name the exact service/version).
3. create_ticket exactly once: severity sev1 if customers are impacted, title
   naming the root cause, body containing your full plan.
Answer with the plan and the ticket id you created."""


async def build_graph():
    client = MultiServerMCPClient({"ops": {"transport": "streamable_http", "url": MCP_URL}})
    tools = await client.get_tools()
    wanted = {"search_runbooks", "create_ticket", "get_recent_deploys"}
    tools = [t for t in tools if t.name in wanted]
    llm = ChatOllama(model=MODEL, temperature=0)
    return create_react_agent(llm, tools, prompt=PROMPT)


if __name__ == "__main__":
    graph = asyncio.run(build_graph())
    card = build_agent_card(
        name="remediation-agent",
        description="Turns a diagnosed root cause into an ordered remediation plan grounded in runbooks, and files the incident ticket.",
        url=f"http://127.0.0.1:{PORT}",
        skills=[AgentSkill(
            id="remediation_planning", name="Remediation planning",
            description="Runbook-grounded fix plan plus incident ticket.",
            tags=["remediation", "runbooks", "ticketing"],
            examples=["Root cause: payment-gateway v2.14.1 leaks DB connections. Plan the fix."],
        )],
    )
    print(f"remediation-agent up on :{PORT} with tools: runbooks, tickets, deploys")
    serve(graph, card, PORT)
