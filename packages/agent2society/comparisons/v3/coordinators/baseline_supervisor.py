"""Baseline LangGraph supervisor that uses Ollama for routing and the a2a-sdk
client to dispatch to real A2A workers.

The supervisor node:
  * Receives the queue of remaining tasks + a running conversation history.
  * Calls Ollama with a system prompt listing the 7 agents and their skills.
  * Parses a JSON routing decision: {agent, skill, task_index}.
  * If the LLM hallucinates an agent that doesn't exist OR JSON parsing
    fails, we record a dispatch_error / hallucinated_agent_routing and skip
    the task (the task is still counted as routed).
  * Otherwise, calls the agent via A2AClient and stores the result.
  * Routes back to itself until tasks_remaining is empty -> END.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypedDict

import httpx

from comparisons.v3.agents import AGENTS_V3
from comparisons.v3.metrics_v3 import RunMetricsV3


def _system_prompt(agent_directory: Dict[str, Dict[str, Any]]) -> str:
    lines = [
        "You are a routing supervisor coordinating 7 specialist agents.",
        "Given the current pending task, choose ONE agent and ONE of their skills.",
        "Reply with STRICTLY a single JSON object on one line, no preamble, no markdown fences:",
        '{"agent": "<agent_name>", "skill": "<skill_id>", "rationale": "<short reason>"}',
        "",
        "Available agents:",
    ]
    for name, cfg in agent_directory.items():
        skills_str = ", ".join(sid for sid, _desc in cfg["skills"])
        lines.append(f"  - {name}: {cfg['description']}")
        lines.append(f"      skills: {skills_str}")
    return "\n".join(lines)


class SupervisorState(TypedDict, total=False):
    tasks_remaining: List[Dict[str, Any]]
    tasks_done: List[Dict[str, Any]]
    conversation_history: List[str]
    routing_history: List[Dict[str, Any]]
    metrics: RunMetricsV3
    agent_urls: Dict[str, str]
    agent_directory: Dict[str, Dict[str, Any]]
    last_routing_text: str
    last_routing_input_tokens: int
    last_routing_output_tokens: int


def _json_decode(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    # Strip code fences if any.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    # Try direct parse first.
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try to find the first JSON object.
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


async def _call_worker(url: str, task: str, skill_id: str = "") -> Dict[str, Any]:
    """Make a real A2A JSON-RPC call. Returns dict {text, meta, rtt_ms, error}."""
    from a2a.client import ClientFactory, ClientConfig
    from a2a.types import SendMessageRequest, Message, Part, Role
    from google.protobuf.struct_pb2 import Struct
    from google.protobuf.json_format import ParseDict, MessageToDict

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=120.0) as httpx_client:
        try:
            factory = ClientFactory(ClientConfig(streaming=False, httpx_client=httpx_client))
            client = await factory.create_from_url(url)
            meta = Struct()
            if skill_id:
                ParseDict({"skill_id": skill_id}, meta)
            req = SendMessageRequest(
                message=Message(
                    message_id=str(uuid.uuid4()),
                    role=Role.ROLE_USER,
                    parts=[Part(text=task)],
                    metadata=meta,
                )
            )
            text = ""
            ret_meta: Dict[str, Any] = {}
            async for resp in client.send_message(req):
                if resp.HasField("message"):
                    for p in resp.message.parts:
                        if p.text:
                            text += p.text
                    if resp.message.metadata:
                        ret_meta = MessageToDict(resp.message.metadata) or {}
                elif resp.HasField("task"):
                    for m in resp.task.history:
                        if m.role == Role.ROLE_AGENT:
                            for p in m.parts:
                                if p.text:
                                    text += p.text
                            if m.metadata:
                                ret_meta = MessageToDict(m.metadata) or {}
            try:
                await client.close()
            except Exception:
                pass
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            return {"text": text, "meta": ret_meta, "rtt_ms": rtt_ms, "error": None}
        except Exception as exc:
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            return {"text": "", "meta": {}, "rtt_ms": rtt_ms, "error": str(exc)}


def _format_context_summary(done: List[Dict[str, Any]], max_chars: int = 240) -> str:
    """Compress prior results so the supervisor pays for context growth but not insanity."""
    out = []
    for i, d in enumerate(done):
        snippet = (d.get("answer") or "")[:max_chars].replace("\n", " ")
        out.append(f"[task {i} -> {d.get('agent')}]: {snippet}")
    return "\n".join(out)


def run_baseline_scenario(
    scenario: Dict[str, Any],
    agent_urls: Dict[str, str],
    *,
    metrics: Optional[RunMetricsV3] = None,
) -> RunMetricsV3:
    """Run one scenario with the LangGraph baseline supervisor."""
    from langgraph.graph import StateGraph, END
    from langchain_ollama import ChatOllama
    from comparisons.v3.agents._shared import make_llm

    metrics = metrics or RunMetricsV3(runner="baseline", scenario_name=scenario["name"])
    metrics.has_explanations = False
    metrics.has_conformance = False
    metrics.has_governance_hooks = False

    # 1) Build the agent directory the supervisor sees -- only the 7 real agents.
    directory: Dict[str, Dict[str, Any]] = {
        name: {
            "description": AGENTS_V3[name]["description"],
            "skills": AGENTS_V3[name]["skills"],
        }
        for name in agent_urls
    }
    sys_prompt = _system_prompt(directory)

    # 2) Define LangGraph nodes.
    def supervisor_node(state: SupervisorState) -> SupervisorState:
        # Pull the next task off the queue and decide a route via Ollama.
        if not state.get("tasks_remaining"):
            return state
        next_task = state["tasks_remaining"][0]
        # Splice in dependency context.
        deps = next_task.get("depends_on", []) or []
        dep_summaries = []
        for di in deps:
            done = state.get("tasks_done", [])
            for d in done:
                if d.get("task_index") == di:
                    dep_summaries.append(d.get("answer", "")[:200])
        ctx = _format_context_summary(state.get("tasks_done", []))
        user_prompt = (
            "Recent history (compressed):\n"
            f"{ctx if ctx else '(none yet)'}\n\n"
            f"Pending task: {next_task['task']}\n"
            "Choose the agent and skill."
        )

        llm = make_llm()
        # Track context size in tokens (approx: input chars / 4)
        approx_in_chars = len(sys_prompt) + len(user_prompt)
        metrics.context_tokens_growth.append(approx_in_chars // 4)

        t0 = time.perf_counter()
        resp = llm.invoke([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ])
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        metrics.supervisor_call_ms.append(elapsed_ms)
        metrics.latency_per_routing_ms.append(elapsed_ms)
        metrics.coordination_calls_total += 1

        usage = getattr(resp, "usage_metadata", None) or {}
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        metrics.coordination_input_tokens += in_tok
        metrics.coordination_output_tokens += out_tok

        state["last_routing_text"] = str(resp.content)
        return state

    async def dispatch_node_async(state: SupervisorState) -> SupervisorState:
        if not state.get("tasks_remaining"):
            return state
        next_task = state["tasks_remaining"].pop(0)
        task_index = next_task.get("task_index")
        text = state.get("last_routing_text", "")
        decision = _json_decode(text) or {}
        chosen_agent = (decision.get("agent") or "").strip()
        chosen_skill = (decision.get("skill") or "").strip()

        agent_was_hallucinated = chosen_agent not in agent_urls and chosen_agent != ""
        json_parse_failed = (not decision) or (not chosen_agent)
        result_text = ""
        worker_meta: Dict[str, Any] = {}
        rtt_ms = 0.0
        error = None

        if json_parse_failed:
            metrics.dispatch_errors += 1
            # Fall back: route to the first agent (silently routes badly).
            chosen_agent = list(agent_urls.keys())[0]
            chosen_skill = ""
            metrics.task_records.append({
                "task_index": task_index,
                "task": next_task["task"],
                "agent": chosen_agent,
                "skill": chosen_skill,
                "decision_text": text,
                "error": "json_parse_failed",
            })
        if agent_was_hallucinated:
            metrics.hallucinated_agent_routings += 1
            metrics.dispatch_errors += 1
            # Fall back to first agent.
            chosen_agent = list(agent_urls.keys())[0]
            metrics.task_records.append({
                "task_index": task_index,
                "task": next_task["task"],
                "agent": chosen_agent,
                "skill": chosen_skill,
                "decision_text": text,
                "error": "hallucinated_agent",
            })

        if chosen_agent in agent_urls:
            res = await _call_worker(agent_urls[chosen_agent], next_task["task"], chosen_skill)
            result_text = res["text"]
            worker_meta = res["meta"]
            rtt_ms = res["rtt_ms"]
            error = res["error"]
            metrics.a2a_rtt_ms.append(rtt_ms)
            if error:
                metrics.dispatch_errors += 1

        # Capture execution tokens from worker metadata
        in_tok = int(float(worker_meta.get("agent_input_tokens", 0) or 0))
        out_tok = int(float(worker_meta.get("agent_output_tokens", 0) or 0))
        metrics.execution_input_tokens += in_tok
        metrics.execution_output_tokens += out_tok

        # Routing accuracy
        expected = next_task.get("expected_agent")
        if expected and chosen_agent == expected:
            metrics.correct_routings += 1
        metrics.num_routing_decisions += 1

        # Audit (baseline only has agent + maybe rationale)
        metrics.record_audit(
            has_agent=bool(chosen_agent),
            has_reason=bool(decision.get("rationale")),
            has_alternatives=False,
            has_runner_up=False,
        )

        state.setdefault("tasks_done", []).append({
            "task_index": task_index,
            "task": next_task["task"],
            "agent": chosen_agent,
            "skill": chosen_skill,
            "answer": result_text,
            "rtt_ms": rtt_ms,
            "error": error,
        })
        if not json_parse_failed and not agent_was_hallucinated:
            metrics.task_records.append({
                "task_index": task_index,
                "task": next_task["task"],
                "agent": chosen_agent,
                "skill": chosen_skill,
                "rationale": decision.get("rationale"),
                "rtt_ms": rtt_ms,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
            })
        return state

    def dispatch_node(state: SupervisorState) -> SupervisorState:
        return asyncio.get_event_loop().run_until_complete(dispatch_node_async(state))

    def should_continue(state: SupervisorState) -> str:
        if state.get("tasks_remaining"):
            return "supervisor"
        return "end"

    # 3) Compile the graph.
    sg = StateGraph(SupervisorState)
    sg.add_node("supervisor", supervisor_node)
    sg.add_node("dispatch", dispatch_node)
    sg.set_entry_point("supervisor")
    sg.add_edge("supervisor", "dispatch")
    sg.add_conditional_edges(
        "dispatch",
        should_continue,
        {"supervisor": "supervisor", "end": END},
    )
    app = sg.compile()

    # 4) Build initial state. The supervisor sees all tasks in order (with their indices).
    tasks_remaining = []
    for i, t in enumerate(scenario["tasks"]):
        tasks_remaining.append({**t, "task_index": i})

    # Time the whole wall clock.
    t_start = time.perf_counter()
    init_state: SupervisorState = {
        "tasks_remaining": tasks_remaining,
        "tasks_done": [],
        "conversation_history": [],
        "routing_history": [],
        "metrics": metrics,
        "agent_urls": agent_urls,
        "agent_directory": directory,
    }
    # LangGraph executes nodes synchronously here.
    app.invoke(init_state, config={"recursion_limit": 200})
    metrics.total_wall_time_ms = (time.perf_counter() - t_start) * 1000.0

    metrics.finalize()
    return metrics
