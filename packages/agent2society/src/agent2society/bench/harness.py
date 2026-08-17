"""Comparative benchmark harness.

Runs the same labeled task suite through:

  * `agent2society.Mesh.run(...)` — coordination via one cheap embedding lookup
    plus a graph traversal.
  * `SupervisorBaseline.run(...)` — coordination via an LLM supervisor that
    reads every agent card in its system prompt.

Both routes dispatch to the same in-process handlers, so the *dispatch*
cost is held equal. The headline number is **coordination tokens per task**.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..adapters import adapt
from ..card import AgentCard, load_card
from ..dispatcher import LocalTransport
from ..exceptions import ConformanceViolation, NoRouteError
from ..mesh import Mesh
from .supervisor import ChatFn, SupervisorBaseline, SupervisorRun, build_supervisor_prompt
from .tasks import TaskSpec
from .tokens import TokenFn, count_tokens, has_tiktoken


@dataclass
class SocietyRun:
    task: str
    chosen_agent: Optional[str]
    chosen_skill: Optional[str]
    coordination_tokens: int  # query embedding token cost
    dispatched: bool
    response_text: str = ""
    conformance_blocked: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "chosen_agent": self.chosen_agent,
            "chosen_skill": self.chosen_skill,
            "coordination_tokens": self.coordination_tokens,
            "dispatched": self.dispatched,
            "conformance_blocked": self.conformance_blocked,
            "error": self.error,
        }


@dataclass
class PairResult:
    spec: TaskSpec
    agent2society: SocietyRun
    sup: SupervisorRun


@dataclass
class BenchResult:
    pairs: List[PairResult] = field(default_factory=list)
    corpus_embed_tokens: int = 0
    supervisor_setup_tokens: int = 0  # constant system-prompt overhead

    # ---- aggregations ----------------------------------------------
    def _sup_tokens(self) -> List[int]:
        return [p.sup.coordination_tokens for p in self.pairs]

    def _agent2society_tokens(self) -> List[int]:
        return [p.agent2society.coordination_tokens for p in self.pairs]

    def supervisor_total_tokens(self) -> int:
        return sum(self._sup_tokens())

    def agent2society_total_tokens(self) -> int:
        return sum(self._agent2society_tokens()) + self.corpus_embed_tokens

    def supervisor_success(self) -> int:
        return sum(
            1
            for p in self.pairs
            if p.sup.chosen_agent == p.spec.expected_agent
            and p.sup.chosen_skill == p.spec.expected_skill
        )

    def agent2society_success(self) -> int:
        return sum(
            1
            for p in self.pairs
            if p.agent2society.chosen_agent == p.spec.expected_agent
            and p.agent2society.chosen_skill == p.spec.expected_skill
        )

    def reduction(self) -> float:
        sup = self.supervisor_total_tokens()
        agent2society_total = self.agent2society_total_tokens()
        if sup == 0:
            return 0.0
        return 1.0 - (agent2society_total / sup)

    # ---- rendering --------------------------------------------------
    def render(
        self,
        *,
        price_per_1k_in: float = 0.00015,
        price_per_1k_out: float = 0.00060,
    ) -> str:
        """Render a comparison table.

        Default pricing reflects gpt-4o-mini at the time of writing
        ($0.15 / 1M input, $0.60 / 1M output). Override for your provider.
        """
        n = len(self.pairs) or 1
        sup_tokens = self._sup_tokens()
        agent2society_tokens = self._agent2society_tokens()
        sup_total = self.supervisor_total_tokens()
        agent2society_total = self.agent2society_total_tokens()

        # Cost estimate: assume 90% of supervisor tokens are prompt, 10%
        # completion (matches the structured-decision shape).
        sup_cost = (
            (sum(p.sup.coord_prompt_tokens for p in self.pairs) / 1000)
            * price_per_1k_in
            + (sum(p.sup.coord_completion_tokens for p in self.pairs) / 1000)
            * price_per_1k_out
        )
        # agent2society coordination cost is embedding-API tokens. Default
        # pricing reflects OpenAI text-embedding-3-small ($0.02 / 1M).
        embed_price_per_1k = 0.00002
        agent2society_cost = (agent2society_total / 1000) * embed_price_per_1k

        def fmt_stats(xs: List[int]) -> str:
            if not xs:
                return "n/a"
            return (
                f"median={int(statistics.median(xs))} "
                f"mean={statistics.mean(xs):.1f} "
                f"min={min(xs)} max={max(xs)}"
            )

        lines: List[str] = []
        lines.append("agent2society vs. native supervisor benchmark")
        lines.append("=" * 60)
        tk = "tiktoken" if has_tiktoken() else "char/4 fallback"
        lines.append(f"tokenizer: {tk}    tasks: {n}")
        lines.append("")
        lines.append("coordination tokens per task")
        lines.append(f"  supervisor:  {fmt_stats(sup_tokens)}")
        lines.append(f"  agent2society:      {fmt_stats(agent2society_tokens)}")
        lines.append("")
        lines.append("totals")
        lines.append(f"  supervisor total:  {sup_total}")
        lines.append(
            f"  agent2society total:      {agent2society_total} "
            f"(corpus={self.corpus_embed_tokens}, queries={sum(agent2society_tokens)})"
        )
        red = self.reduction()
        lines.append(f"  reduction:         {red*100:.2f}%")
        lines.append("")
        lines.append("estimated coordination cost (USD)")
        lines.append(f"  supervisor:  ${sup_cost:.6f}")
        lines.append(f"  agent2society:      ${agent2society_cost:.6f}")
        if sup_cost > 0:
            lines.append(f"  ratio:       {sup_cost / max(agent2society_cost, 1e-12):.1f}x")
        lines.append("")
        lines.append("task success (right (agent, skill) chosen)")
        lines.append(f"  supervisor:  {self.supervisor_success()}/{n}")
        lines.append(f"  agent2society:      {self.agent2society_success()}/{n}")
        lines.append("")
        lines.append("per-task")
        for p in self.pairs:
            sup_ok = (
                p.sup.chosen_agent == p.spec.expected_agent
                and p.sup.chosen_skill == p.spec.expected_skill
            )
            our_ok = (
                p.agent2society.chosen_agent == p.spec.expected_agent
                and p.agent2society.chosen_skill == p.spec.expected_skill
            )
            head = p.spec.task
            if len(head) > 60:
                head = head[:57] + "..."
            lines.append(
                f"  {head}"
            )
            lines.append(
                f"    expected: {p.spec.expected_agent}::{p.spec.expected_skill}"
            )
            lines.append(
                f"    sup    -> {p.sup.chosen_agent}::{p.sup.chosen_skill} "
                f"[{'ok' if sup_ok else 'MISS'}, coord={p.sup.coordination_tokens}]"
            )
            lines.append(
                f"    agent2society -> {p.agent2society.chosen_agent}::{p.agent2society.chosen_skill} "
                f"[{'ok' if our_ok else 'MISS'}, coord={p.agent2society.coordination_tokens}]"
            )
        return "\n".join(lines)


class Bench:
    """The harness.

    Construct with a list of cards (or native agents) and a list of tasks.
    Call `.run()` to get a BenchResult.
    """

    def __init__(
        self,
        *,
        cards: Sequence[Any],
        tasks: Sequence[TaskSpec],
        chat_fn: Optional[ChatFn] = None,
        token_fn: Optional[TokenFn] = None,
        embed_query_token_fn: Optional[Callable[[str], int]] = None,
    ) -> None:
        self._tasks = list(tasks)
        self._token_fn = token_fn or count_tokens
        # Cost of embedding ONE query at API call time. Embedding-3-small
        # tokenizes similarly to cl100k_base, so reuse the LLM token fn.
        self._embed_token_fn = embed_query_token_fn or self._token_fn

        # Build a shared Mesh so the in-process handlers are wired once and
        # both methods dispatch through the same transport.
        self._mesh = Mesh(strict=False)
        self._cards: List[AgentCard] = []
        for c in cards:
            card = self._mesh.add(c)
            self._cards.append(card)

        # The supervisor baseline reuses the same graph and local transport,
        # so dispatch cost is held equal.
        self._supervisor = SupervisorBaseline(
            self._mesh.graph,
            chat_fn=chat_fn,
            local_transport=self._mesh._local,
            token_fn=self._token_fn,
        )

    # ---- one-time costs --------------------------------------------
    def _corpus_embed_tokens(self) -> int:
        """Tokens the user pays to embed the skill corpus once."""
        total = 0
        for node in self._mesh.graph.agents():
            for s in node.card.skills:
                text = " ".join([node.card.description, s.search_text()]).strip()
                total += self._embed_token_fn(text)
        return total

    # ---- run --------------------------------------------------------
    def run(self) -> BenchResult:
        result = BenchResult(corpus_embed_tokens=self._corpus_embed_tokens())
        for spec in self._tasks:
            our_run = self._run_agent2society(spec)
            sup_run = self._supervisor.run(spec.task)
            result.pairs.append(PairResult(spec=spec, agent2society=our_run, sup=sup_run))
        return result

    def _run_agent2society(self, spec: TaskSpec) -> SocietyRun:
        query_tokens = self._embed_token_fn(spec.task)
        try:
            text = self._mesh.run(spec.task, tags=spec.tags)
        except (NoRouteError, ConformanceViolation) as e:
            # Mesh is in strict=False so this branch is unlikely, but
            # keep it for robustness.
            last = self._mesh.telemetry.records[-1]
            return SocietyRun(
                task=spec.task,
                chosen_agent=last.chosen_agent,
                chosen_skill=last.chosen_skill,
                coordination_tokens=query_tokens,
                dispatched=False,
                conformance_blocked=last.violation is not None,
                error=str(e),
            )
        last = self._mesh.telemetry.records[-1]
        return SocietyRun(
            task=spec.task,
            chosen_agent=last.chosen_agent,
            chosen_skill=last.chosen_skill,
            coordination_tokens=query_tokens,
            dispatched=last.dispatched,
            response_text=text,
            conformance_blocked=last.violation is not None,
            error=last.error,
        )
