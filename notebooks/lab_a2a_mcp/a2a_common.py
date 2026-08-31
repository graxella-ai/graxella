"""Boilerplate needed to expose one LangGraph agent over the A2A protocol.

Every line here is glue a developer must hand-write today: wrap the graph in an
AgentExecutor, translate LangChain messages <-> A2A protobuf, build the agent
card, mount JSON-RPC + REST + card routes on FastAPI, run uvicorn.
"""
import uvicorn
from fastapi import FastAPI

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill


class LangGraphAgentExecutor(AgentExecutor):
    """Bridges an A2A request into a LangGraph agent and back."""

    def __init__(self, graph, name: str):
        self.graph = graph
        self.name = name

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.get_user_input()  # A2A protobuf Message -> plain text
        print(f"[{self.name}] A2A task in: {task[:120]!r}")
        result = await self.graph.ainvoke(
            {"messages": [{"role": "user", "content": task}]},
            {"recursion_limit": 40},
        )
        reply = result["messages"][-1].content  # LangChain message -> text
        n_tool_calls = sum(len(getattr(m, "tool_calls", []) or []) for m in result["messages"])
        print(f"[{self.name}] done: {len(result['messages'])} messages, {n_tool_calls} tool calls")
        # text -> A2A protobuf Message (role defaults to ROLE_AGENT)
        await event_queue.enqueue_event(new_text_message(reply, context_id=context.context_id))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def build_agent_card(name: str, description: str, url: str, skills: list[AgentSkill]) -> AgentCard:
    return AgentCard(
        name=name,
        description=description,
        version="1.0.0",
        supported_interfaces=[AgentInterface(url=url, protocol_binding="JSONRPC")],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=skills,
    )


def serve(graph, card: AgentCard, port: int) -> None:
    handler = DefaultRequestHandler(
        agent_executor=LangGraphAgentExecutor(graph, card.name),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
        rest_routes=create_rest_routes(handler),
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
