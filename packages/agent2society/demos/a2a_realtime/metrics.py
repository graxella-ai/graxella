"""Token, cost, and latency helpers for the A2A comparison.

Both orchestrators use deterministic routing today (keyword overlap on the
a2a_only side, TF-IDF in agent2society), so their *measured* coordination
token usage is zero. But that hides the real-world cost story: developers
who pick pure A2A almost always pair it with an LLM supervisor (LangGraph,
hand-rolled GPT-4o routing, etc.) because keyword matching doesn't scale.

This module models two things:

  1. **Execution tokens** -- the work the chosen agent actually does. Same
     formula for both pipelines (same agents, same payload), so this row
     is a sanity check that we're routing to similar agents.

  2. **Projected coordination tokens** -- what the a2a_only side would
     burn if it used a realistic LangGraph supervisor instead of the toy
     keyword matcher. agent2society's coordination overhead is the
     in-process TF-IDF vector dot product -- zero LLM tokens by construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


# ---------------------------------------------------------------------------
# Public list prices per 1M tokens (mid-2026 demo values, matches v2 schema)
# ---------------------------------------------------------------------------

PRICE_GPT4O_MINI_IN = 0.15
PRICE_GPT4O_MINI_OUT = 0.60
PRICE_GPT4O_IN = 5.00
PRICE_GPT4O_OUT = 15.00
PRICE_CLAUDE_OPUS_4_IN = 15.00
PRICE_CLAUDE_OPUS_4_OUT = 75.00


def usd(input_tokens: int, output_tokens: int, p_in: float, p_out: float) -> float:
    return (input_tokens / 1_000_000.0) * p_in + (output_tokens / 1_000_000.0) * p_out


def cost_gpt4o_mini(tin: int, tout: int) -> float:
    return usd(tin, tout, PRICE_GPT4O_MINI_IN, PRICE_GPT4O_MINI_OUT)


def cost_gpt4o(tin: int, tout: int) -> float:
    return usd(tin, tout, PRICE_GPT4O_IN, PRICE_GPT4O_OUT)


def cost_claude_opus_4(tin: int, tout: int) -> float:
    return usd(tin, tout, PRICE_CLAUDE_OPUS_4_IN, PRICE_CLAUDE_OPUS_4_OUT)


# ---------------------------------------------------------------------------
# Token modelling
# ---------------------------------------------------------------------------

# Realistic LangGraph-style supervisor system prompt for 4 agents with
# tag/description listings. Calibrated by measuring the same prompt in
# B11_A2A_Layer_build/comparisons/baseline_langgraph.py with tiktoken.
SUPERVISOR_PROMPT_TOKENS = 312
SUPERVISOR_OUTPUT_TOKENS = 50   # one JSON line: {"agent": "...", "skill": "..."}


def supervisor_coord_tokens(ticket_text: str) -> tuple[int, int]:
    """Tokens an LLM supervisor would burn to route one ticket.

    Used as the *projected* coord cost for the a2a_only side -- the toy
    keyword matcher in the demo is free, but no production pure-A2A stack
    routes that way. The projection makes the real-world cost gap visible.
    """
    words = len(ticket_text.split())
    ticket_tokens = int(words * 1.33) + 5
    return SUPERVISOR_PROMPT_TOKENS + ticket_tokens, SUPERVISOR_OUTPUT_TOKENS


def execution_tokens(task_text: str) -> tuple[int, int]:
    """Tokens the chosen agent burns to answer.

    Same formula as B11_A2A_Layer_build/comparisons/agents.py so the demo
    numbers line up with the historical benchmark.
    """
    words = len(task_text.split())
    inp = int(words * 1.33) + 15
    out = words * 8 + 60
    return inp, out


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------

def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


# ---------------------------------------------------------------------------
# Aggregate container
# ---------------------------------------------------------------------------

@dataclass
class Aggregate:
    name: str
    # routing quality
    correct: int = 0
    total: int = 0
    # tokens (measured + projected)
    measured_coord_in: int = 0
    measured_coord_out: int = 0
    projected_coord_in: int = 0
    projected_coord_out: int = 0
    exec_in: int = 0
    exec_out: int = 0
    # latency
    latencies: List[float] = None
    wall_time_ms: float = 0.0
    # routing transparency
    decisions_with_rationale: int = 0
    decisions_with_margin: int = 0
    decisions_with_runner_up: int = 0
    # governance
    governance_alerts: int = 0
    pii_blocked: int = 0
    # reliability
    dispatch_errors: int = 0

    def __post_init__(self):
        if self.latencies is None:
            self.latencies = []

    # ---- derived ----
    @property
    def routing_accuracy(self) -> float:
        return (self.correct / self.total) if self.total else 0.0

    @property
    def measured_total_tokens(self) -> int:
        return (
            self.measured_coord_in + self.measured_coord_out
            + self.exec_in + self.exec_out
        )

    @property
    def projected_total_tokens(self) -> int:
        return (
            self.projected_coord_in + self.projected_coord_out
            + self.exec_in + self.exec_out
        )

    @property
    def measured_cost_gpt4o_mini(self) -> float:
        return cost_gpt4o_mini(
            self.measured_coord_in + self.exec_in,
            self.measured_coord_out + self.exec_out,
        )

    @property
    def projected_cost_gpt4o_mini(self) -> float:
        return cost_gpt4o_mini(
            self.projected_coord_in + self.exec_in,
            self.projected_coord_out + self.exec_out,
        )

    @property
    def projected_cost_gpt4o(self) -> float:
        return cost_gpt4o(
            self.projected_coord_in + self.exec_in,
            self.projected_coord_out + self.exec_out,
        )

    @property
    def projected_cost_claude_opus_4(self) -> float:
        return cost_claude_opus_4(
            self.projected_coord_in + self.exec_in,
            self.projected_coord_out + self.exec_out,
        )

    @property
    def p50_ms(self) -> float:
        return percentile(self.latencies, 50.0)

    @property
    def p95_ms(self) -> float:
        return percentile(self.latencies, 95.0)

    @property
    def p99_ms(self) -> float:
        return percentile(self.latencies, 99.0)

    @property
    def max_ms(self) -> float:
        return max(self.latencies) if self.latencies else 0.0

    @property
    def throughput(self) -> float:
        if self.wall_time_ms <= 0 or not self.total:
            return 0.0
        return self.total / (self.wall_time_ms / 1000.0)

    @property
    def audit_completeness(self) -> float:
        """0..1: fraction of (rationale, margin, runner-up) recorded per decision."""
        if not self.total:
            return 0.0
        slots = self.total * 3
        hits = (
            self.decisions_with_rationale
            + self.decisions_with_margin
            + self.decisions_with_runner_up
        )
        return hits / slots
