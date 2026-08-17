"""LangGraph supervisor baseline.

A *real* LangGraph supervisor — not a hand-rolled mock — using
`langgraph.graph.StateGraph` and a counted fake chat model. Coordination
tokens are measured by tokenising the actual prompts LangGraph sends.

Pattern (canonical for LangGraph 1.x):
    START -> supervisor -> {agent_1, agent_2, ..., END}
            ^________________________|
                   each agent returns control to supervisor

Each call to the supervisor LLM:
  * prompt = system message enumerating every agent + conversation history
  * response = a structured decision picking the next agent or "FINISH"

This is the cost shape agent2society avoids by precomputing skill vectors and
matching them against one embedding of each sub-task.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import END, START, StateGraph

from ..card import AgentCard, load_card
from .tokens import TokenFn, count_tokens


# ---- supervisor state -----------------------------------------------

class SupervisorState(TypedDict, total=False):
    """The shared state carried through the LangGraph supervisor."""

    task: str
    messages: List[BaseMessage]
    next: str
    step_count: int


# ---- counted fake chat model ----------------------------------------

class CountedFakeChat(BaseChatModel):
    """A fake LangChain chat model that records realistic token costs.

    On each `invoke`, the model:
      1. Tokenises the inbound messages with the supplied `token_fn` and
         records the count.
      2. Returns the next pre-programmed response (so correctness is held
         equal to the agent2society side).
      3. Records the response's token count.

    The total `prompt_tokens + completion_tokens` per call is what a real
    LangGraph supervisor would have paid against the same provider, given
    these prompts.
    """

    # Pydantic v2-friendly model config
    model_config = {"arbitrary_types_allowed": True}

    responses: List[AIMessage]
    token_fn: Any  # Callable[[str], int]
    calls: List[Dict[str, Any]] = []

    def __init__(
        self,
        *,
        responses: Sequence[AIMessage],
        token_fn: TokenFn,
    ) -> None:
        super().__init__(
            responses=list(responses),
            token_fn=token_fn,
            calls=[],
        )

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt_text = "\n".join(
            (m.content if isinstance(m.content, str) else str(m.content))
            for m in messages
        )
        prompt_tokens = self.token_fn(prompt_text)

        if not self.responses:
            # Default to FINISH if we run out — keeps tests robust.
            reply = AIMessage(content="FINISH")
        else:
            reply = self.responses.pop(0)
        completion_tokens = self.token_fn(reply.content or "")

        self.calls.append(
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "messages": [m.content for m in messages],
            }
        )
        return ChatResult(generations=[ChatGeneration(message=reply)])

    @property
    def _llm_type(self) -> str:
        return "counted_fake"

    def total_tokens(self) -> int:
        return sum(c["prompt_tokens"] + c["completion_tokens"] for c in self.calls)

    def total_prompt_tokens(self) -> int:
        return sum(c["prompt_tokens"] for c in self.calls)

    def total_completion_tokens(self) -> int:
        return sum(c["completion_tokens"] for c in self.calls)


# ---- supervisor prompt builder --------------------------------------

SUPERVISOR_SYSTEM_TEMPLATE = (
    "You are the supervisor of a multi-agent team. Given the user's overall "
    "objective and the conversation so far, decide which single team member "
    "should act next. When the objective is fully complete, respond with "
    "FINISH instead of routing.\n\n"
    "Respond with ONE of:\n"
    '  {{"next": "<agent-name>"}}\n'
    '  {{"next": "FINISH"}}\n\n'
    "Available team members:\n{agent_list}\n"
)


def _render_agent_list(cards: Sequence[AgentCard]) -> str:
    blocks: List[str] = []
    for i, card in enumerate(cards, 1):
        lines = [f"{i}. {card.name}"]
        if card.description:
            lines.append(f"   description: {card.description}")
        lines.append("   skills:")
        for s in card.skills:
            lines.append(f"     - {s.id}: {s.description or s.name}")
            if s.tags:
                lines.append(f"       tags: {', '.join(s.tags)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ---- decision parsing -----------------------------------------------

_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_next(text: str) -> str:
    """Pull `next` out of the LLM reply, tolerantly."""
    import json

    s = (text or "").strip()
    if s.upper().endswith("FINISH") or s.strip().upper() == "FINISH":
        return "FINISH"
    for cand in [s, *_JSON_RE.findall(s)]:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "next" in data:
            val = str(data["next"]).strip()
            return val or "FINISH"
    # Fall back to substring scan for a literal agent name.
    return "FINISH"


# ---- the baseline ---------------------------------------------------

@dataclass
class LangGraphRun:
    task: str
    steps_taken: List[str] = field(default_factory=list)
    coordination_prompt_tokens: int = 0
    coordination_completion_tokens: int = 0
    supervisor_calls: int = 0
    final_messages: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def coordination_tokens(self) -> int:
        return self.coordination_prompt_tokens + self.coordination_completion_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "steps_taken": list(self.steps_taken),
            "supervisor_calls": self.supervisor_calls,
            "coord_prompt_tokens": self.coordination_prompt_tokens,
            "coord_completion_tokens": self.coordination_completion_tokens,
            "coordination_tokens": self.coordination_tokens,
            "error": self.error,
        }


class LangGraphSupervisor:
    """Real LangGraph supervisor over a fixed set of agents.

    `agent_runner` is a callable invoked when the supervisor routes to an
    agent. It receives `(agent_name, task)` and returns a string — the
    agent's response. This lets the harness reuse the same in-process
    handlers agent2society uses, so dispatch cost is held equal between the two
    methods.

    `decisions` is the pre-programmed list of routing decisions the fake
    supervisor LLM will return. Each element is one of:
      * an agent name (e.g. "research-agent") — routes there
      * "FINISH" — ends the run
    """

    def __init__(
        self,
        cards: Sequence[Any],
        *,
        decisions: Sequence[str],
        agent_runner: Callable[[str, str], str],
        token_fn: Optional[TokenFn] = None,
        recursion_limit: int = 50,
    ) -> None:
        self._cards: List[AgentCard] = [load_card(c) for c in cards]
        self._token_fn = token_fn or count_tokens
        self._agent_runner = agent_runner
        self._recursion_limit = recursion_limit
        self._system_prompt = SUPERVISOR_SYSTEM_TEMPLATE.format(
            agent_list=_render_agent_list(self._cards)
        )

        # Pre-program the fake LLM with structured decisions plus a final
        # FINISH so the graph terminates cleanly.
        responses: List[AIMessage] = []
        for d in decisions:
            if d == "FINISH":
                responses.append(AIMessage(content='{"next": "FINISH"}'))
            else:
                responses.append(AIMessage(content=f'{{"next": "{d}"}}'))
        if not responses or responses[-1].content != '{"next": "FINISH"}':
            responses.append(AIMessage(content='{"next": "FINISH"}'))

        self._llm = CountedFakeChat(responses=responses, token_fn=self._token_fn)
        self._graph = self._build_graph()

    # ---- graph construction --------------------------------------
    def _supervisor_node(self, state: SupervisorState) -> Dict[str, Any]:
        history = state.get("messages", [])
        msgs: List[BaseMessage] = [SystemMessage(content=self._system_prompt)]
        msgs.append(HumanMessage(content=f"Objective: {state['task']}"))
        msgs.extend(history)
        reply = self._llm.invoke(msgs)
        nxt = _parse_next(reply.content if isinstance(reply.content, str) else "")
        # Validate the picked agent exists; otherwise FINISH to avoid loops.
        valid_names = {c.name for c in self._cards}
        if nxt != "FINISH" and nxt not in valid_names:
            nxt = "FINISH"
        return {"next": nxt}

    def _make_agent_node(self, name: str) -> Callable[[SupervisorState], Dict[str, Any]]:
        def node(state: SupervisorState) -> Dict[str, Any]:
            task = state["task"]
            history = state.get("messages", [])
            # The agent sees the original objective plus prior agent outputs;
            # we pass the most recent guidance text as its sub-task.
            sub_task = task
            for msg in reversed(history):
                if isinstance(msg, HumanMessage):
                    sub_task = msg.content
                    break
            text = self._agent_runner(name, sub_task)
            new_msg = AIMessage(content=f"[{name}] {text}", name=name)
            count = state.get("step_count", 0) + 1
            return {
                "messages": history + [new_msg],
                "step_count": count,
            }

        return node

    def _route(self, state: SupervisorState) -> str:
        nxt = state.get("next", "FINISH")
        return nxt if nxt != "FINISH" else END

    def _build_graph(self):
        g = StateGraph(SupervisorState)
        g.add_node("supervisor", self._supervisor_node)
        for c in self._cards:
            g.add_node(c.name, self._make_agent_node(c.name))
        g.add_edge(START, "supervisor")
        edge_map: Dict[str, str] = {c.name: c.name for c in self._cards}
        edge_map["FINISH"] = END
        # conditional edges from supervisor to either an agent or END
        g.add_conditional_edges(
            "supervisor",
            self._route,
            {**{c.name: c.name for c in self._cards}, END: END},
        )
        for c in self._cards:
            g.add_edge(c.name, "supervisor")
        return g.compile()

    # ---- runner --------------------------------------------------
    def run(self, task: str) -> LangGraphRun:
        run = LangGraphRun(task=task)
        try:
            final = self._graph.invoke(
                {"task": task, "messages": [], "step_count": 0},
                config={"recursion_limit": self._recursion_limit},
            )
        except Exception as e:
            run.error = f"{type(e).__name__}: {e}"
            # Even on failure, count whatever the LLM did spend.
            run.supervisor_calls = len(self._llm.calls)
            run.coordination_prompt_tokens = self._llm.total_prompt_tokens()
            run.coordination_completion_tokens = self._llm.total_completion_tokens()
            return run
        run.supervisor_calls = len(self._llm.calls)
        run.coordination_prompt_tokens = self._llm.total_prompt_tokens()
        run.coordination_completion_tokens = self._llm.total_completion_tokens()
        msgs = final.get("messages", [])
        run.final_messages = [
            m.content if isinstance(m.content, str) else str(m.content) for m in msgs
        ]
        for m in msgs:
            if isinstance(m, AIMessage) and m.name:
                run.steps_taken.append(m.name)
        return run
