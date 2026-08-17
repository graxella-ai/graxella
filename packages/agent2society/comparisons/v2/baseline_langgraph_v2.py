"""Baseline runner: REAL langgraph.StateGraph + multi-turn supervisor.

This is the fair-baseline LangGraph supervisor pattern:
  * One typed `State` carrying messages + remaining/done tasks +
    current_result + conversation history.
  * Nodes: supervisor, research_agent, analysis_agent, writer_agent,
    data_agent, plus an END sentinel.
  * Conditional edges: supervisor -> worker (by routing decision);
    every worker -> supervisor (to re-plan); supervisor -> END when no
    tasks remain.
  * For each task, the supervisor is invoked TWICE: once to ROUTE the
    task, once to CONFIRM (decide whether the worker's result needs a
    follow-up).
  * Under the hood, the supervisor "LLM" uses the SAME TF-IDF scorer as
    agent2society (via shared_router) -- so accuracy is identical and
    we only measure coordination overhead.
"""
from __future__ import annotations

import os
import random
import time
import tracemalloc
from typing import Any, Callable, Dict, List, Optional, TypedDict

import psutil
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from .agents_v2 import AGENT_REGISTRY, agent_catalog_text, execute_agent
from .metrics_v2 import RunMetricsV2
from .shared_router import RouteDecision, build_shared_router


# ---------------------------------------------------------------------------
# Typed state carried across LangGraph nodes
# ---------------------------------------------------------------------------

class State(TypedDict, total=False):
    messages: List[BaseMessage]
    tasks_remaining: List[Dict[str, Any]]
    tasks_done: List[Dict[str, Any]]
    current_result: str
    conversation_history: List[str]
    routing_history: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Tokenized supervisor "LLM" wrapper
# ---------------------------------------------------------------------------

# Per-task baseline costs (in tokens) for the cumulative-history grow term.
HISTORY_TOKENS_PER_PRIOR_STEP = 80


class CountedSupervisorChat:
    """A fake supervisor LLM that counts tokens like a real one would.

    Per call:
      input_tokens  = int(len(full_prompt.split()) * 1.33)
      output_tokens = 180 + random.randint(0, 50)   (seeded)

    The system prompt is the full agent / skill catalog (~350 tokens).
    Each prior step adds HISTORY_TOKENS_PER_PRIOR_STEP to context.

    Under the hood we DON'T do any LLM reasoning -- we delegate to the
    shared TF-IDF router. The token count is what we'd pay for a real LLM
    making the same call.
    """

    def __init__(
        self,
        *,
        shared_route: Callable[[str], RouteDecision],
        seed: int = 1337,
    ) -> None:
        self._route = shared_route
        self._rng = random.Random(seed)
        self.input_tokens_total = 0
        self.output_tokens_total = 0
        self.context_tokens_per_call: List[int] = []
        self.calls_total = 0
        self.latencies_ms: List[float] = []
        # Per-call decision and latency are also returned in invoke().
        self.system_prompt = agent_catalog_text()

    def _count_input_tokens(self, full_prompt: str) -> int:
        return int(len(full_prompt.split()) * 1.33)

    def invoke(
        self,
        messages: List[BaseMessage],
        *,
        task_text: str,
        mode: str,
    ) -> RouteDecision:
        """Mode is 'route' or 'confirm'. Both are paid LLM calls."""
        t0 = time.perf_counter()

        full_prompt = " ".join(
            m.content for m in messages if isinstance(m.content, str)
        )
        input_tokens = self._count_input_tokens(full_prompt)
        output_tokens = 180 + self._rng.randint(0, 50)

        # Under-the-hood routing (no real LLM needed; this is what we
        # would have paid the LLM for): only the route mode actually
        # produces a routing decision. The confirm mode "decides" task
        # completion -- we always return the same decision dict.
        if mode == "route":
            decision = self._route(task_text)
        else:
            # confirm mode just emits a deterministic "done" decision.
            decision = RouteDecision(
                agent="(none)",
                skill="(none)",
                score=0.0,
                margin=0.0,
                semantic=0.0,
                tag_overlap=0.0,
                alternatives=[],
                runner_up_reason=None,
            )

        self.input_tokens_total += input_tokens
        self.output_tokens_total += output_tokens
        self.context_tokens_per_call.append(input_tokens)
        self.calls_total += 1

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        # Add a small, deterministic baseline LLM-latency simulation.
        # Real supervisor LLM calls are far slower than our compute, but
        # we don't want to inflate -- we add a tiny constant so the wall
        # time isn't pinned to zero.
        elapsed_ms += 0.5  # ms overhead per supervisor call
        self.latencies_ms.append(elapsed_ms)
        return decision


# ---------------------------------------------------------------------------
# LangGraph node factories
# ---------------------------------------------------------------------------

WORKER_NODES = ["research_agent", "analysis_agent", "writer_agent", "data_agent"]


def _supervisor_node_factory(
    counted: CountedSupervisorChat,
    metrics: RunMetricsV2,
) -> Callable[[State], Dict[str, Any]]:
    """Supervisor node: routes the next task OR confirms completion.

    Each task burns TWO supervisor calls in this runner:
      1. mode=route       (pre-dispatch routing decision)
      2. mode=confirm     (post-dispatch acknowledgement)
    """

    def supervisor(state: State) -> Dict[str, Any]:
        remaining = state.get("tasks_remaining") or []
        done = state.get("tasks_done") or []
        history = state.get("conversation_history") or []
        routing_history = state.get("routing_history") or []
        prior_result = state.get("current_result", "")
        messages = state.get("messages") or []

        # Confirm step: we have a current_result from the last worker
        # AND it has not yet been confirmed (tracked via routing_history).
        last_routing = routing_history[-1] if routing_history else None
        needs_confirm = (
            last_routing is not None and not last_routing.get("confirmed")
        )

        if needs_confirm:
            # ---- CONFIRMATION CALL (the second supervisor turn) ----
            history_block = "\n".join(history[-10:])
            confirm_prompt = (
                f"Conversation so far:\n{history_block}\n\n"
                f"The {last_routing['agent']} just returned a result for the "
                f"task: '{last_routing['task'][:120]}'.\n"
                f"Decide if this is complete or needs a follow-up. "
                f"Reply DONE or REROUTE."
            )
            confirm_msgs = [
                SystemMessage(content=counted.system_prompt),
                HumanMessage(content=confirm_prompt),
            ]
            counted.invoke(confirm_msgs, task_text=prior_result, mode="confirm")
            last_routing["confirmed"] = True
            history.append(f"supervisor: confirmed {last_routing['agent']} task")
            ai = AIMessage(content="DONE")
            return {
                "messages": messages + [ai],
                "routing_history": routing_history,
                "conversation_history": history,
            }

        # ---- END condition: no tasks remaining ----
        if not remaining:
            return {
                "messages": messages,
                "tasks_remaining": [],
                "tasks_done": done,
                "routing_history": routing_history,
                "conversation_history": history,
            }

        # ---- ROUTING CALL (first supervisor turn for this task) ----
        next_task = remaining[0]
        history_block = "\n".join(history[-10:])
        history_pad = "x " * (HISTORY_TOKENS_PER_PRIOR_STEP * len(done))
        route_prompt = (
            f"Conversation so far:\n{history_block}\n\n"
            f"PRIOR_CONTEXT_PADDING:\n{history_pad}\n"
            f"NEW TASK:\n{next_task['task']}\n\n"
            "Decide which (agent, skill) pair is most appropriate and emit "
            'JSON of the form {"agent": "...", "skill": "...", "reason": "..."}.'
        )
        route_msgs = [
            SystemMessage(content=counted.system_prompt),
            HumanMessage(content=route_prompt),
        ]
        decision = counted.invoke(
            route_msgs, task_text=next_task["task"], mode="route"
        )

        # Audit recording (baseline produces partial audit info: the
        # chosen agent + a one-line reason; alternatives + runner-up
        # are NOT recorded by a real supervisor LLM unless we explicitly
        # asked it to enumerate them, which most teams don't do).
        metrics.record_audit(
            has_agent=True,
            has_reason=True,
            has_alternatives=False,
            has_runner_up=False,
        )

        history.append(
            f"supervisor: routed -> {decision.agent}::{decision.skill}"
        )
        routing_entry = {
            "task": next_task["task"],
            "agent": decision.agent,
            "skill": decision.skill,
            "score": decision.score,
            "confirmed": False,
        }
        routing_history.append(routing_entry)

        ai = AIMessage(
            content=f'{{"agent": "{decision.agent}", "skill": "{decision.skill}"}}'
        )
        return {
            "messages": messages + [ai],
            "tasks_remaining": remaining,
            "tasks_done": done,
            "routing_history": routing_history,
            "conversation_history": history,
        }

    return supervisor


def _worker_node_factory(
    agent_id: str, metrics: RunMetricsV2
) -> Callable[[State], Dict[str, Any]]:
    """Worker node: executes the current task using the chosen skill."""

    def worker(state: State) -> Dict[str, Any]:
        remaining = list(state.get("tasks_remaining") or [])
        done = list(state.get("tasks_done") or [])
        routing_history = state.get("routing_history") or []
        history = state.get("conversation_history") or []
        messages = state.get("messages") or []

        if not remaining or not routing_history:
            return {}

        next_task = remaining[0]
        last_routing = routing_history[-1]
        skill = last_routing["skill"]

        try:
            result_text, inp_tok, out_tok = execute_agent(
                agent_id, skill, next_task["task"]
            )
            metrics.execution_input_tokens += inp_tok
            metrics.execution_output_tokens += out_tok
        except Exception as exc:
            result_text = f"[ERROR] {exc}"
            metrics.dispatch_errors += 1

        # advance the queue
        next_task_done = dict(next_task)
        next_task_done["agent"] = agent_id
        next_task_done["skill"] = skill
        done.append(next_task_done)
        remaining = remaining[1:]

        history.append(f"{agent_id}: {result_text[:80]}")

        return {
            "messages": messages + [AIMessage(content=result_text[:200])],
            "tasks_remaining": remaining,
            "tasks_done": done,
            "current_result": result_text,
            "routing_history": routing_history,
            "conversation_history": history,
        }

    return worker


def _decide_next(state: State) -> str:
    """Conditional routing: from the supervisor, branch to the worker
    indicated by the latest routing entry, OR to END when finished."""
    remaining = state.get("tasks_remaining") or []
    routing_history = state.get("routing_history") or []

    if not remaining and (
        not routing_history or routing_history[-1].get("confirmed")
    ):
        return "END"

    if routing_history and not routing_history[-1].get("confirmed"):
        # we just routed -- dispatch to the worker
        agent = routing_history[-1]["agent"]
        if agent in WORKER_NODES:
            return agent
        # unknown agent -- fall back to analysis
        return "analysis_agent"

    # default: loop to supervisor for confirm step
    return "supervisor"


# ---------------------------------------------------------------------------
# Build + compile graph
# ---------------------------------------------------------------------------

def build_graph(
    counted: CountedSupervisorChat, metrics: RunMetricsV2
):
    builder = StateGraph(State)
    builder.add_node("supervisor", _supervisor_node_factory(counted, metrics))
    for agent_id in WORKER_NODES:
        builder.add_node(agent_id, _worker_node_factory(agent_id, metrics))

    builder.set_entry_point("supervisor")
    builder.add_conditional_edges(
        "supervisor",
        _decide_next,
        {
            **{a: a for a in WORKER_NODES},
            "supervisor": "supervisor",
            "END": END,
        },
    )
    # Every worker goes back to supervisor to confirm + then re-plan
    for agent_id in WORKER_NODES:
        builder.add_edge(agent_id, "supervisor")

    return builder.compile()


# ---------------------------------------------------------------------------
# Cold-start measurement
# ---------------------------------------------------------------------------

def measure_cold_start() -> float:
    """Time it takes to construct a brand-new compiled graph + router."""
    t0 = time.perf_counter()
    shared_route = build_shared_router(AGENT_REGISTRY)
    counted = CountedSupervisorChat(shared_route=shared_route)
    # build but don't invoke
    dummy_metrics = RunMetricsV2(runner="cold-start-probe", scenario_name="probe")
    build_graph(counted, dummy_metrics)
    return (time.perf_counter() - t0) * 1000.0


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def run_scenario(scenario: Dict[str, Any], *, seed: int = 1337) -> RunMetricsV2:
    metrics = RunMetricsV2(
        runner="LangGraph Baseline v2",
        scenario_name=scenario["name"],
        has_explanations=False,
        has_conformance=False,
        has_governance_hooks=False,
    )

    proc = psutil.Process(os.getpid())
    mem_before_mb = proc.memory_info().rss / 1024 / 1024

    cold = time.perf_counter()
    shared_route = build_shared_router(AGENT_REGISTRY)
    counted = CountedSupervisorChat(shared_route=shared_route, seed=seed)
    graph = build_graph(counted, metrics)
    metrics.cold_start_ms = (time.perf_counter() - cold) * 1000.0

    initial_state: State = {
        "messages": [],
        "tasks_remaining": list(scenario["tasks"]),
        "tasks_done": [],
        "current_result": "",
        "conversation_history": [],
        "routing_history": [],
    }

    n_tasks = len(scenario["tasks"])
    # LangGraph default recursion limit is 25; we need ~3 supervisor +
    # worker transitions per task. Give plenty of headroom.
    recursion_limit = max(100, n_tasks * 8)

    start_wall = time.perf_counter()
    first_dispatch_recorded = False
    first_dispatch_t0 = start_wall

    final_state = graph.invoke(
        initial_state, config={"recursion_limit": recursion_limit}
    )
    elapsed_ms = (time.perf_counter() - start_wall) * 1000.0

    # Walk the routing history to extract per-task records and accuracy.
    routing_history = final_state.get("routing_history") or []
    done_tasks = final_state.get("tasks_done") or []

    # Pair each routing decision with the original scenario task.
    for i, scen_task in enumerate(scenario["tasks"]):
        if i < len(routing_history):
            rh = routing_history[i]
            chosen_agent = rh["agent"]
            chosen_skill = rh["skill"]
        else:
            chosen_agent = "analysis_agent"
            chosen_skill = "statistical_analysis"

        correct = chosen_agent == scen_task["expected_agent"]
        metrics.task_records.append(
            {
                "task": scen_task["task"][:80],
                "agent": chosen_agent,
                "skill": chosen_skill,
                "expected_agent": scen_task["expected_agent"],
                "expected_skill": scen_task["expected_skill"],
                "correct": correct,
                "boundary_test": scen_task.get("boundary_test", False),
            }
        )
        metrics.num_routing_decisions += 1
        if correct:
            metrics.correct_routings += 1

    if not first_dispatch_recorded and counted.latencies_ms:
        # cold-to-first-dispatch ~= cold + first supervisor call
        metrics.time_to_first_dispatch_ms = (
            metrics.cold_start_ms + counted.latencies_ms[0]
        )

    mem_after_mb = proc.memory_info().rss / 1024 / 1024
    metrics.peak_memory_mb = max(mem_before_mb, mem_after_mb)

    metrics.total_wall_time_ms = elapsed_ms
    metrics.coordination_input_tokens = counted.input_tokens_total
    metrics.coordination_output_tokens = counted.output_tokens_total
    metrics.coordination_calls_total = counted.calls_total
    metrics.latency_per_routing_ms = list(counted.latencies_ms)
    metrics.context_tokens_growth = list(counted.context_tokens_per_call)

    # Baseline has NO conformance / governance / flags / hooks
    # (those values stay at their dataclass defaults).
    metrics.finalize()
    return metrics
