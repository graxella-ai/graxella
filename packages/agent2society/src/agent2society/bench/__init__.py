"""agent2society benchmark harness.

Compare agent2society routing against the native A2A supervisor pattern on
coordination tokens per task, holding correctness equal.

    from agent2society.bench import Bench, default_suite, default_mesh_cards

    bench = Bench(
        cards=default_mesh_cards(),
        tasks=default_suite(),
    )
    result = bench.run()
    print(result.render())

For real-LLM measurements, pass `chat_fn=` — any callable that takes a
list of OpenAI-style messages and returns a dict containing `content`
(and optionally `usage`). See `agent2society.bench.supervisor` for details.
"""
from __future__ import annotations

from .harness import Bench, BenchResult, SocietyRun, PairResult

# Optional: LangGraph-backed baseline. Imported lazily because langgraph is
# only required when actually running the head-to-head benchmark.
try:
    from .langgraph_baseline import (
        CountedFakeChat,
        LangGraphRun,
        LangGraphSupervisor,
    )
    from .multistep import (
        SocietyScenarioRun,
        MultiStepBench,
        MultiStepResult,
        ScenarioResult,
        default_agent_runner,
    )
    from .scenarios import Scenario, default_scenarios

    _HAVE_LANGGRAPH = True
except ImportError:  # pragma: no cover - exercised when extras missing
    _HAVE_LANGGRAPH = False
from .supervisor import (
    ChatFn,
    SupervisorBaseline,
    SupervisorRun,
    build_supervisor_prompt,
    make_mock_chat_fn,
    parse_supervisor_decision,
    render_mesh_for_supervisor,
)
from .tasks import TaskSpec, default_mesh_cards, default_suite
from .tokens import count_tokens, has_tiktoken, make_token_fn

__all__ = [
    "Bench",
    "BenchResult",
    "PairResult",
    "SocietyRun",
    "ChatFn",
    "SupervisorBaseline",
    "SupervisorRun",
    "TaskSpec",
    "build_supervisor_prompt",
    "make_mock_chat_fn",
    "parse_supervisor_decision",
    "render_mesh_for_supervisor",
    "default_suite",
    "default_mesh_cards",
    "count_tokens",
    "has_tiktoken",
    "make_token_fn",
]
