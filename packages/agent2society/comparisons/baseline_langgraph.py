"""Baseline LangGraph supervisor implementation.

No agent2society imports. Simulates the classic LLM-supervisor pattern
where every routing decision burns LLM tokens to decide which agent to call.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterator, List, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from .agents import AGENT_REGISTRY, execute_agent
from .metrics import RunMetrics

# ---------------------------------------------------------------------------
# Supervisor system-prompt builder (~300 token realistic prompt)
# ---------------------------------------------------------------------------

def _build_supervisor_prompt() -> str:
    lines = [
        "You are a multi-agent orchestration supervisor.",
        "Given a user task, you MUST route it to exactly one agent and one skill.",
        "Reply ONLY with valid JSON: {\"agent\": \"<agent_id>\", \"skill\": \"<skill_id>\"}",
        "",
        "Available agents and skills:",
    ]
    for agent_id, agent_info in AGENT_REGISTRY.items():
        lines.append(f"\nAgent: {agent_id}")
        lines.append(f"  Description: {agent_info['description']}")
        lines.append("  Skills:")
        for skill_id, skill_desc in agent_info["skills"].items():
            lines.append(f"    - {skill_id}: {skill_desc}")
    lines.append(
        "\nChoose the MOST APPROPRIATE agent and skill for the task."
        " Reply with JSON only."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic routing logic: keyword matching against skill descriptions
# ---------------------------------------------------------------------------

def _deterministic_route(task_text: str) -> tuple[str, str]:
    """Pick agent + skill by keyword overlap with skill descriptions.

    This simulates a real LLM supervisor that has seen the system prompt
    and picks the best match deterministically (for benchmark reproducibility).
    The matching logic is intentionally limited -- the same imperfections a
    real LLM supervisor would exhibit when skill descriptions are dense.
    """
    task_lower = task_text.lower()
    task_words = set(task_lower.split())

    best_agent = None
    best_skill = None
    best_score = -1

    for agent_id, agent_info in AGENT_REGISTRY.items():
        for skill_id, skill_desc in agent_info["skills"].items():
            desc_words = set(skill_desc.lower().split())
            # Remove very common stop words from matching
            stop = {"a", "an", "the", "and", "or", "of", "to", "in", "for",
                    "on", "by", "at", "is", "are", "that", "this", "with",
                    "it", "as", "be", "from", "has", "have", "its"}
            desc_words -= stop
            task_check = task_words - stop
            overlap = len(desc_words & task_check)
            if overlap > best_score:
                best_score = overlap
                best_agent = agent_id
                best_skill = skill_id

    # Default fallback
    if best_agent is None or best_score == 0:
        best_agent = "analysis_agent"
        best_skill = "statistical_analysis"

    return best_agent, best_skill  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Fake LLM that counts tokens without making real API calls
# ---------------------------------------------------------------------------

class CountedFakeChat(BaseChatModel):
    """Deterministic fake chat model that counts supervisor tokens.

    Each call costs:
      input_tokens  = ceil(len(full_prompt.split()) * 1.33)
      output_tokens = 50  (fixed per routing call)
    """

    model_name: str = "fake-supervisor"

    # cumulative counters (mutable state via list trick to bypass frozen)
    _input_total: List[int] = []
    _output_total: List[int] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Use object.__setattr__ to bypass Pydantic v2 frozen model restriction
        object.__setattr__(self, "_input_total", [0])
        object.__setattr__(self, "_output_total", [0])

    @property
    def total_input_tokens(self) -> int:
        return self._input_total[0]

    @property
    def total_output_tokens(self) -> int:
        return self._output_total[0]

    @property
    def _llm_type(self) -> str:
        return "fake-supervisor"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        full_prompt = " ".join(m.content for m in messages if isinstance(m.content, str))
        input_tokens = int(len(full_prompt.split()) * 1.33)
        output_tokens = 50

        self._input_total[0] += input_tokens
        self._output_total[0] += output_tokens

        # Route deterministically based on the last human message
        task_text = messages[-1].content if messages else ""
        agent_id, skill_id = _deterministic_route(task_text)

        routing_json = json.dumps({"agent": agent_id, "skill": skill_id})
        message = AIMessage(content=routing_json)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        yield result.generations[0]


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def run_scenario(scenario: dict) -> RunMetrics:
    """Run a single scenario using the LangGraph supervisor pattern."""
    metrics = RunMetrics(
        runner="LangGraph Baseline",
        scenario_name=scenario["name"],
        has_explanations=False,
        has_conformance=False,
        has_governance_hooks=False,
    )

    supervisor_prompt = _build_supervisor_prompt()
    llm = CountedFakeChat()

    # Pre-count supervisor system prompt tokens (counted once per scenario
    # as it's loaded into context for every task).
    # Each task includes: system prompt + task text -> input tokens.

    start = time.perf_counter()

    for task_info in scenario["tasks"]:
        task_text = task_info["task"]
        expected_agent = task_info["expected_agent"]
        expected_skill = task_info["expected_skill"]

        # Build full prompt for token counting
        from langchain_core.messages import HumanMessage, SystemMessage
        messages = [
            SystemMessage(content=supervisor_prompt),
            HumanMessage(content=f"Route this task to the correct agent:\n{task_text}"),
        ]

        # LLM call (counts tokens, returns routing decision)
        result = llm._generate(messages)
        routing_json = result.generations[0].message.content

        try:
            routing = json.loads(routing_json)
            chosen_agent = routing.get("agent", "analysis_agent")
            chosen_skill = routing.get("skill", "statistical_analysis")
        except (json.JSONDecodeError, AttributeError):
            chosen_agent = "analysis_agent"
            chosen_skill = "statistical_analysis"

        # Execute the chosen agent
        _result_text, inp_tok, out_tok = execute_agent(chosen_agent, chosen_skill, task_text)

        correct = (chosen_agent == expected_agent)
        metrics.task_records.append({
            "task": task_text[:60],
            "agent": chosen_agent,
            "skill": chosen_skill,
            "expected_agent": expected_agent,
            "expected_skill": expected_skill,
            "correct": correct,
            "exec_input_tokens": inp_tok,
            "exec_output_tokens": out_tok,
        })
        metrics.execution_tokens += inp_tok + out_tok
        metrics.num_routing_decisions += 1
        if correct:
            metrics.correct_routings += 1

    elapsed = (time.perf_counter() - start) * 1000
    metrics.elapsed_ms = elapsed
    metrics.coordination_tokens = llm.total_input_tokens + llm.total_output_tokens
    metrics.finalize()
    return metrics
