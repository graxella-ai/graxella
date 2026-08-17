"""Multi-step head-to-head harness: agent2society vs. real LangGraph supervisor.

For each Scenario:

  * LangGraph supervisor is given the high-level objective and the agent
    roster, then routes itself step-by-step (one supervisor LLM call per
    decision, with growing conversation state).
  * agent2society is given the pre-decomposed sub-tasks (the v1 design says the
    user does decomposition; the planner is out of scope). It pays one
    embedding lookup per sub-task plus the one-time corpus embed.

Same in-process handlers; dispatch cost is held equal. The headline is
coordination cost per scenario.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..card import load_card
from ..mesh import Mesh
from .langgraph_baseline import LangGraphRun, LangGraphSupervisor
from .scenarios import Scenario
from .tokens import TokenFn, count_tokens, has_tiktoken


# Shared agent runner: returns canned text so both methods produce the
# same agent-side output. Coordination cost is the only variable.
def default_agent_runner(agent_name: str, sub_task: str) -> str:
    return f"{agent_name} completed sub-task: {sub_task}"


# ---- per-scenario results -------------------------------------------

@dataclass
class SocietyScenarioRun:
    scenario_name: str
    steps_attempted: int
    steps_correct: int
    coordination_tokens: int
    dispatched: int
    error: Optional[str] = None


@dataclass
class ScenarioResult:
    scenario: Scenario
    agent2society: SocietyScenarioRun
    langgraph: LangGraphRun

    @property
    def langgraph_correct(self) -> int:
        expected = [a for (a, _) in self.scenario.expected_steps]
        steps = self.langgraph.steps_taken
        # Count positionally — supervisor must hit the right agent in
        # the right order for a step to count as "correct".
        correct = 0
        for exp, actual in zip(expected, steps):
            if exp == actual:
                correct += 1
        return correct


@dataclass
class MultiStepResult:
    scenarios: List[ScenarioResult] = field(default_factory=list)
    corpus_embed_tokens: int = 0

    # ---- aggregations ---------------------------------------------
    def supervisor_total_tokens(self) -> int:
        return sum(s.langgraph.coordination_tokens for s in self.scenarios)

    def agent2society_total_tokens(self) -> int:
        return (
            sum(s.agent2society.coordination_tokens for s in self.scenarios)
            + self.corpus_embed_tokens
        )

    def total_steps(self) -> int:
        return sum(s.scenario.step_count for s in self.scenarios)

    def supervisor_steps_correct(self) -> int:
        return sum(s.langgraph_correct for s in self.scenarios)

    def agent2society_steps_correct(self) -> int:
        return sum(s.agent2society.steps_correct for s in self.scenarios)

    def reduction(self) -> float:
        sup = self.supervisor_total_tokens()
        if sup == 0:
            return 0.0
        return 1.0 - (self.agent2society_total_tokens() / sup)

    # ---- rendering ------------------------------------------------
    def render(
        self,
        *,
        price_per_1k_in: float = 0.00015,
        price_per_1k_out: float = 0.00060,
        embed_price_per_1k: float = 0.00002,
    ) -> str:
        sup_prompt = sum(
            s.langgraph.coordination_prompt_tokens for s in self.scenarios
        )
        sup_compl = sum(
            s.langgraph.coordination_completion_tokens for s in self.scenarios
        )
        sup_total = self.supervisor_total_tokens()
        agent2society_total = self.agent2society_total_tokens()
        sup_cost = (
            (sup_prompt / 1000) * price_per_1k_in
            + (sup_compl / 1000) * price_per_1k_out
        )
        agent2society_cost = (agent2society_total / 1000) * embed_price_per_1k

        n_scen = len(self.scenarios)
        total_steps = self.total_steps()
        sup_calls = sum(s.langgraph.supervisor_calls for s in self.scenarios)

        lines: List[str] = []
        lines.append("agent2society vs. real LangGraph supervisor (multi-step)")
        lines.append("=" * 64)
        tk = "tiktoken" if has_tiktoken() else "char/4 fallback"
        lines.append(
            f"tokenizer: {tk}    scenarios: {n_scen}    total steps: {total_steps}"
        )
        lines.append("")
        lines.append("coordination tokens (sum across scenarios)")
        lines.append(
            f"  langgraph supervisor:  {sup_total}  "
            f"({sup_calls} supervisor LLM calls)"
        )
        lines.append(
            f"  agent2society:                {agent2society_total}  "
            f"(corpus={self.corpus_embed_tokens}, queries="
            f"{agent2society_total - self.corpus_embed_tokens})"
        )
        red = self.reduction()
        lines.append(f"  reduction:             {red * 100:.2f}%")
        lines.append("")
        lines.append("estimated coordination cost (USD)")
        lines.append(f"  langgraph:  ${sup_cost:.6f}")
        lines.append(f"  agent2society:     ${agent2society_cost:.6f}")
        if agent2society_cost > 0:
            lines.append(f"  ratio:      {sup_cost / max(agent2society_cost, 1e-12):.1f}x")
        lines.append("")
        lines.append("step success (right agent in right order)")
        lines.append(
            f"  langgraph:  {self.supervisor_steps_correct()}/{total_steps}"
        )
        lines.append(
            f"  agent2society:     {self.agent2society_steps_correct()}/{total_steps}"
        )
        lines.append("")
        lines.append("per-scenario detail")
        for s in self.scenarios:
            sc = s.scenario
            lines.append(f"  [{sc.name}] ({sc.step_count} steps)")
            lines.append(f"    objective: {sc.objective[:88]}...")
            lg = s.langgraph
            lc = s.agent2society
            lines.append(
                f"    langgraph:  supervisor_calls={lg.supervisor_calls}  "
                f"coord_tokens={lg.coordination_tokens}  "
                f"steps_correct={s.langgraph_correct}/{sc.step_count}"
            )
            lines.append(
                f"    agent2society:     coord_tokens={lc.coordination_tokens}  "
                f"steps_correct={lc.steps_correct}/{sc.step_count}"
            )
            if lg.error:
                lines.append(f"    langgraph error: {lg.error}")
            if lc.error:
                lines.append(f"    agent2society error:    {lc.error}")
        return "\n".join(lines)


# ---- harness --------------------------------------------------------

class MultiStepBench:
    def __init__(
        self,
        *,
        cards: Sequence[Any],
        scenarios: Sequence[Scenario],
        agent_runner: Optional[Callable[[str, str], str]] = None,
        token_fn: Optional[TokenFn] = None,
    ) -> None:
        self._cards = [load_card(c) for c in cards]
        self._scenarios = list(scenarios)
        self._token_fn = token_fn or count_tokens
        self._runner = agent_runner or default_agent_runner

        # Build the agent2society mesh once. Both methods route to the same in-
        # process handler so dispatch cost is identical.
        self._mesh = Mesh(strict=False)
        for c in self._cards:
            self._mesh.add(c)
        for node in self._mesh.graph.agents():
            name = node.name

            def make_handler(n: str):
                def handler(_url, payload):
                    msg_id = payload.get("id", "0")
                    # Pull the sub-task text from the payload to feed the runner.
                    params = payload.get("params") or {}
                    msg = params.get("message") or {}
                    parts = msg.get("parts") or []
                    sub = " ".join(p.get("text", "") for p in parts if isinstance(p, dict))
                    text = self._runner(n, sub)
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "parts": [{"kind": "text", "text": text}],
                        },
                    }

                return handler

            self._mesh._local.register(node.url, make_handler(name))

    def _corpus_embed_tokens(self) -> int:
        total = 0
        for node in self._mesh.graph.agents():
            for s in node.card.skills:
                text = " ".join([node.card.description, s.search_text()]).strip()
                total += self._token_fn(text)
        return total

    def _run_agent2society(self, scenario: Scenario) -> SocietyScenarioRun:
        coord = 0
        dispatched = 0
        correct = 0
        err: Optional[str] = None
        for expected_agent, sub_task in scenario.expected_steps:
            coord += self._token_fn(sub_task)
            try:
                self._mesh.run(sub_task)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                continue
            last = self._mesh.telemetry.records[-1]
            if last.dispatched:
                dispatched += 1
            if last.chosen_agent == expected_agent:
                correct += 1
        return SocietyScenarioRun(
            scenario_name=scenario.name,
            steps_attempted=scenario.step_count,
            steps_correct=correct,
            coordination_tokens=coord,
            dispatched=dispatched,
            error=err,
        )

    def _run_langgraph(self, scenario: Scenario) -> LangGraphRun:
        # The fake LangGraph supervisor is pre-programmed to make the
        # ground-truth routing decisions, so correctness is held equal to
        # agent2society. We are measuring cost, not LLM IQ.
        decisions = [a for (a, _) in scenario.expected_steps] + ["FINISH"]
        sup = LangGraphSupervisor(
            self._cards,
            decisions=decisions,
            agent_runner=self._runner,
            token_fn=self._token_fn,
        )
        return sup.run(scenario.objective)

    def run(self) -> MultiStepResult:
        result = MultiStepResult(corpus_embed_tokens=self._corpus_embed_tokens())
        for sc in self._scenarios:
            lc = self._run_agent2society(sc)
            lg = self._run_langgraph(sc)
            result.scenarios.append(
                ScenarioResult(scenario=sc, agent2society=lc, langgraph=lg)
            )
        return result
