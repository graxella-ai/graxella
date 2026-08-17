"""Shared utilities for all v3 worker agents.

Each worker has:
  * A LangGraph StateGraph with prepare_context -> analyze nodes.
  * The analyze node calls ChatOllama and captures usage_metadata tokens.
  * An A2A AgentExecutor that runs the graph and replies with both the
    text + a `agent_tokens` payload in message metadata.
  * A uvicorn-hosted A2AStarletteApplication on the agent's port.

The model choice is read from env var V3_MODEL (default qwen2.5:7b).
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, TypedDict

# Make sure the local src/ is on the path even when run as `python -m ...` from a subprocess.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def get_model_name() -> str:
    return os.environ.get("V3_MODEL", "qwen2.5:7b")


def make_llm(temperature: float = 0.0):
    from langchain_ollama import ChatOllama
    return ChatOllama(model=get_model_name(), temperature=temperature)


class WorkerState(TypedDict, total=False):
    incoming_task: str
    skill_id: str
    agent_name: str
    agent_description: str
    skills_blurb: str
    prepared_prompt: str
    answer: str
    input_tokens: int
    output_tokens: int


def build_worker_graph(agent_name: str, agent_description: str, skills: List[tuple]):
    """Compile a 2-node LangGraph for the worker."""
    from langgraph.graph import StateGraph, END

    skills_blurb = "\n".join(f"  - {sid}: {desc}" for sid, _name, desc in
                              [(s[0], s[0], s[1]) for s in skills])

    def prepare_context(state: WorkerState) -> WorkerState:
        # Deterministic node: format the prompt with role + skill list.
        task = state.get("incoming_task", "")
        skill_id = state.get("skill_id", "")
        prompt = (
            f"You are the {agent_name}. {agent_description}\n"
            f"Available skills:\n{skills_blurb}\n"
            f"Skill requested: {skill_id or '(unspecified)'}\n"
            f"Task: {task}\n\n"
            f"Respond concisely (2-4 sentences) with your professional analysis. "
            f"Be specific and actionable. Cite the skill you used."
        )
        return {
            **state,
            "agent_name": agent_name,
            "agent_description": agent_description,
            "skills_blurb": skills_blurb,
            "prepared_prompt": prompt,
        }

    def analyze(state: WorkerState) -> WorkerState:
        llm = make_llm()
        prompt = state.get("prepared_prompt", "")
        resp = llm.invoke(prompt)
        usage = getattr(resp, "usage_metadata", None) or {}
        return {
            **state,
            "answer": str(resp.content),
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
        }

    sg = StateGraph(WorkerState)
    sg.add_node("prepare_context", prepare_context)
    sg.add_node("analyze", analyze)
    sg.set_entry_point("prepare_context")
    sg.add_edge("prepare_context", "analyze")
    sg.add_edge("analyze", END)
    return sg.compile()


def build_agent_card(name: str, port: int, description: str, skills: List[tuple]):
    """Build an a2a-sdk AgentCard for this worker."""
    from a2a.types import (
        AgentCard,
        AgentSkill,
        AgentCapabilities,
        AgentInterface,
    )

    skill_objs = [
        AgentSkill(
            id=sid,
            name=sid,
            description=desc,
            tags=[name, sid],
        )
        for (sid, desc) in skills
    ]
    card = AgentCard(
        name=name,
        description=description,
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supported_interfaces=[
            AgentInterface(
                url=f"http://localhost:{port}/",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        skills=skill_objs,
    )
    return card


def run_worker_server(
    agent_name: str, port: int, description: str, skills: List[tuple]
) -> None:
    """Subprocess entrypoint. Boots the FastAPI app and serves forever."""
    import uvicorn
    import json
    import time
    from fastapi import FastAPI
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.events import EventQueue
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import (
        create_agent_card_routes,
        create_jsonrpc_routes,
    )
    from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import Message, Part, Role

    graph = build_worker_graph(agent_name, description, skills)

    class WorkerExecutor(AgentExecutor):
        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            user_text = ""
            skill_id = ""
            metadata = {}
            if context.message is not None:
                # Pull text and metadata.
                for p in context.message.parts:
                    if p.text:
                        user_text += p.text
                if context.message.metadata:
                    try:
                        # message metadata is a google.protobuf.Struct
                        from google.protobuf.json_format import MessageToDict
                        metadata = MessageToDict(context.message.metadata) or {}
                    except Exception:
                        metadata = {}
                skill_id = metadata.get("skill_id", "") if isinstance(metadata, dict) else ""

            t0 = time.perf_counter()
            try:
                result = await graph.ainvoke({
                    "incoming_task": user_text,
                    "skill_id": skill_id,
                })
                answer = result.get("answer", "")
                in_tok = int(result.get("input_tokens", 0))
                out_tok = int(result.get("output_tokens", 0))
            except Exception as exc:
                answer = f"[error in {agent_name}: {exc}]"
                in_tok = 0
                out_tok = 0
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            from google.protobuf.struct_pb2 import Struct
            from google.protobuf.json_format import ParseDict
            meta = Struct()
            ParseDict({
                "agent_name": agent_name,
                "agent_input_tokens": in_tok,
                "agent_output_tokens": out_tok,
                "agent_elapsed_ms": elapsed_ms,
                "agent_skill_used": skill_id,
            }, meta)

            reply = Message(
                message_id=str(uuid.uuid4()),
                context_id=context.context_id or "",
                role=Role.ROLE_AGENT,
                parts=[Part(text=answer)],
                metadata=meta,
            )
            await event_queue.enqueue_event(reply)

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            return None

    card = build_agent_card(agent_name, port, description, skills)

    app = FastAPI(title=f"a2a-worker:{agent_name}")
    handler = DefaultRequestHandler(
        agent_executor=WorkerExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(
            handler, rpc_url="/", enable_v0_3_compat=True
        ),
    )

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
