"""Expanded quantitative metrics for v2 comparisons.

Tracks:
  * cost (tokens + USD across three model prices)
  * latency (per-routing, p50/p95/p99/max, wall time, throughput)
  * coordination overhead (calls, calls/task, context-token growth)
  * routing quality (a2s flags, confidence/margin distributions)
  * governance (conformance violations caught, hook firings)
  * transparency / auditability (decisions explained, alternatives, runner-up)
  * reliability (errors, retries, cold start, peak memory)
  * determinism (re-run agreement across 3 seeds)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Model pricing (per 1M tokens) -- public list prices as of mid-2026 demo.
# ---------------------------------------------------------------------------

PRICE_GPT4O_MINI_IN = 0.15
PRICE_GPT4O_MINI_OUT = 0.60
PRICE_GPT4O_IN = 5.00
PRICE_GPT4O_OUT = 15.00
PRICE_CLAUDE_OPUS_4_IN = 15.00
PRICE_CLAUDE_OPUS_4_OUT = 75.00


def _usd(input_tokens: int, output_tokens: int, p_in: float, p_out: float) -> float:
    return (input_tokens / 1_000_000.0) * p_in + (output_tokens / 1_000_000.0) * p_out


def _pct(numerator: int, denominator: int) -> float:
    return (numerator / denominator) if denominator else 0.0


def _percentile(values: List[float], p: float) -> float:
    """Plain linear-interpolation percentile (avoids numpy dependency)."""
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
# Per-run metrics
# ---------------------------------------------------------------------------

@dataclass
class RunMetricsV2:
    """All measurements from a single scenario run by one runner."""

    runner: str
    scenario_name: str

    # ---- per-task records (the raw audit log) ----
    task_records: List[Dict[str, Any]] = field(default_factory=list)
    explanations: List[str] = field(default_factory=list)

    # ---- cost ----
    coordination_input_tokens: int = 0
    coordination_output_tokens: int = 0
    coordination_tokens_total: int = 0
    execution_input_tokens: int = 0
    execution_output_tokens: int = 0
    execution_tokens_total: int = 0
    total_tokens: int = 0
    cost_usd_gpt4o_mini: float = 0.0
    cost_usd_gpt4o: float = 0.0
    cost_usd_claude_opus_4: float = 0.0

    # ---- latency ----
    latency_per_routing_ms: List[float] = field(default_factory=list)
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    latency_max_ms: float = 0.0
    time_to_first_dispatch_ms: float = 0.0
    total_wall_time_ms: float = 0.0
    throughput_tasks_per_sec: float = 0.0

    # ---- coordination overhead ----
    coordination_calls_total: int = 0
    coordination_calls_per_task: float = 0.0
    context_tokens_growth: List[int] = field(default_factory=list)
    avg_context_tokens_first_half: float = 0.0
    avg_context_tokens_second_half: float = 0.0

    # ---- routing quality (a2s) ----
    flags_low_margin_count: int = 0
    flags_ood_count: int = 0
    flags_vector_ambiguity_count: int = 0
    confidence_distribution: List[float] = field(default_factory=list)
    margin_distribution: List[float] = field(default_factory=list)
    runner_up_capture_rate: float = 0.0

    # ---- governance (a2s) ----
    conformance_violations_caught: int = 0
    low_confidence_hook_fired_count: int = 0
    conflict_hook_fired_count: int = 0
    capability_drift_hook_fired_count: int = 0

    # ---- transparency / auditability ----
    decisions_with_explanation: int = 0
    decisions_with_alternatives_recorded: int = 0
    decisions_with_runner_up_reason: int = 0
    audit_completeness_score: float = 0.0

    # ---- reliability ----
    dispatch_errors: int = 0
    dispatch_retries: int = 0
    cold_start_ms: float = 0.0
    peak_memory_mb: float = 0.0

    # ---- determinism (populated by orchestrator) ----
    determinism_score: float = 1.0
    num_routing_decisions: int = 0

    # ---- routing accuracy (note: with shared_router this is identical
    #      across runners, so it functions as a sanity check) ----
    correct_routings: int = 0
    routing_accuracy: float = 0.0

    # ---- capability flags for the feature-matrix display ----
    has_explanations: bool = False
    has_conformance: bool = False
    has_governance_hooks: bool = False

    # ---- internal: which records had at least 3-of-4 audit fields ----
    _audit_field_hits: List[int] = field(default_factory=list)

    # -------------------------------------------------------------------
    # finalize
    # -------------------------------------------------------------------
    def finalize(self) -> None:
        self.coordination_tokens_total = (
            self.coordination_input_tokens + self.coordination_output_tokens
        )
        self.execution_tokens_total = (
            self.execution_input_tokens + self.execution_output_tokens
        )
        self.total_tokens = (
            self.coordination_tokens_total + self.execution_tokens_total
        )

        total_in = self.coordination_input_tokens + self.execution_input_tokens
        total_out = self.coordination_output_tokens + self.execution_output_tokens
        self.cost_usd_gpt4o_mini = _usd(
            total_in, total_out, PRICE_GPT4O_MINI_IN, PRICE_GPT4O_MINI_OUT
        )
        self.cost_usd_gpt4o = _usd(
            total_in, total_out, PRICE_GPT4O_IN, PRICE_GPT4O_OUT
        )
        self.cost_usd_claude_opus_4 = _usd(
            total_in,
            total_out,
            PRICE_CLAUDE_OPUS_4_IN,
            PRICE_CLAUDE_OPUS_4_OUT,
        )

        if self.latency_per_routing_ms:
            self.latency_p50_ms = _percentile(self.latency_per_routing_ms, 50.0)
            self.latency_p95_ms = _percentile(self.latency_per_routing_ms, 95.0)
            self.latency_p99_ms = _percentile(self.latency_per_routing_ms, 99.0)
            self.latency_max_ms = max(self.latency_per_routing_ms)

        if self.num_routing_decisions > 0:
            self.coordination_calls_per_task = (
                self.coordination_calls_total / self.num_routing_decisions
            )
            self.routing_accuracy = (
                self.correct_routings / self.num_routing_decisions
            )
        if self.total_wall_time_ms > 0 and self.num_routing_decisions > 0:
            self.throughput_tasks_per_sec = (
                self.num_routing_decisions / (self.total_wall_time_ms / 1000.0)
            )

        # context growth split
        if self.context_tokens_growth:
            n = len(self.context_tokens_growth)
            mid = max(1, n // 2)
            first = self.context_tokens_growth[:mid]
            second = self.context_tokens_growth[mid:] or first
            self.avg_context_tokens_first_half = (
                statistics.fmean(first) if first else 0.0
            )
            self.avg_context_tokens_second_half = (
                statistics.fmean(second) if second else 0.0
            )

        # audit completeness: average over per-decision hit counts (0..4)
        if self._audit_field_hits:
            self.audit_completeness_score = (
                statistics.fmean(self._audit_field_hits) / 4.0
            )

        # runner-up capture rate (only meaningful if there were decisions)
        if self.num_routing_decisions > 0:
            self.runner_up_capture_rate = (
                self.decisions_with_runner_up_reason / self.num_routing_decisions
            )

    # -------------------------------------------------------------------
    # helper: record per-decision audit completeness
    # -------------------------------------------------------------------
    def record_audit(
        self,
        *,
        has_agent: bool,
        has_reason: bool,
        has_alternatives: bool,
        has_runner_up: bool,
    ) -> None:
        hits = sum(
            [
                int(has_agent),
                int(has_reason),
                int(has_alternatives),
                int(has_runner_up),
            ]
        )
        self._audit_field_hits.append(hits)
        if has_reason:
            self.decisions_with_explanation += 1
        if has_alternatives:
            self.decisions_with_alternatives_recorded += 1
        if has_runner_up:
            self.decisions_with_runner_up_reason += 1


# ---------------------------------------------------------------------------
# Pretty printer for a side-by-side per-scenario report
# ---------------------------------------------------------------------------

class ComparisonReportV2:
    def __init__(self, baseline: RunMetricsV2, a2s: RunMetricsV2) -> None:
        self.baseline = baseline
        self.a2s = a2s

    @staticmethod
    def _saved_pct(base: float, new: float) -> str:
        if base == 0:
            return "N/A"
        saved = base - new
        pct = saved / base * 100.0
        sign = "-" if pct >= 0 else "+"
        return f"{sign}{abs(pct):.1f}%"

    @staticmethod
    def _icon(v: bool) -> str:
        return "YES" if v else " NO"

    def print_report(self) -> None:
        b = self.baseline
        a = self.a2s

        W = 84
        title = f"  SCENARIO: {b.scenario_name}  "
        print()
        print("=" * W)
        print(title.center(W))
        print("=" * W)

        col_label = 38
        col_base = 20
        col_a2s = 20

        def row(label: str, bval: str, aval: str) -> None:
            print(f"  {label:<{col_label}} {bval:>{col_base}} {aval:>{col_a2s}}")

        def section(title: str) -> None:
            print()
            print(f"  [{title}]")
            print("  " + "-" * (W - 4))

        # Header
        print(
            f"  {'Metric':<{col_label}} "
            f"{'LangGraph Baseline':>{col_base}} "
            f"{'agent2society':>{col_a2s}}"
        )
        print("  " + "-" * (W - 4))

        section("COST")
        row(
            "Coordination tokens (in+out)",
            f"{b.coordination_tokens_total:,}",
            f"{a.coordination_tokens_total:,}",
        )
        row(
            "Execution tokens",
            f"{b.execution_tokens_total:,}",
            f"{a.execution_tokens_total:,}",
        )
        row("Total tokens", f"{b.total_tokens:,}", f"{a.total_tokens:,}")
        row("  Token delta", "", self._saved_pct(b.total_tokens, a.total_tokens))
        row(
            "Cost USD (gpt-4o-mini)",
            f"${b.cost_usd_gpt4o_mini:.6f}",
            f"${a.cost_usd_gpt4o_mini:.6f}",
        )
        row(
            "Cost USD (gpt-4o)",
            f"${b.cost_usd_gpt4o:.6f}",
            f"${a.cost_usd_gpt4o:.6f}",
        )
        row(
            "Cost USD (claude-opus-4)",
            f"${b.cost_usd_claude_opus_4:.6f}",
            f"${a.cost_usd_claude_opus_4:.6f}",
        )

        section("LATENCY")
        row("p50 routing ms", f"{b.latency_p50_ms:.3f}", f"{a.latency_p50_ms:.3f}")
        row("p95 routing ms", f"{b.latency_p95_ms:.3f}", f"{a.latency_p95_ms:.3f}")
        row("p99 routing ms", f"{b.latency_p99_ms:.3f}", f"{a.latency_p99_ms:.3f}")
        row("max routing ms", f"{b.latency_max_ms:.3f}", f"{a.latency_max_ms:.3f}")
        row(
            "Time to first dispatch ms",
            f"{b.time_to_first_dispatch_ms:.3f}",
            f"{a.time_to_first_dispatch_ms:.3f}",
        )
        row(
            "Total wall time ms",
            f"{b.total_wall_time_ms:.3f}",
            f"{a.total_wall_time_ms:.3f}",
        )
        row(
            "Throughput tasks/sec",
            f"{b.throughput_tasks_per_sec:.2f}",
            f"{a.throughput_tasks_per_sec:.2f}",
        )

        section("COORDINATION OVERHEAD")
        row(
            "Supervisor / routing calls",
            f"{b.coordination_calls_total}",
            f"{a.coordination_calls_total}",
        )
        row(
            "Calls per task",
            f"{b.coordination_calls_per_task:.2f}",
            f"{a.coordination_calls_per_task:.2f}",
        )
        row(
            "Avg context tokens (first half)",
            f"{b.avg_context_tokens_first_half:.0f}",
            f"{a.avg_context_tokens_first_half:.0f}",
        )
        row(
            "Avg context tokens (second half)",
            f"{b.avg_context_tokens_second_half:.0f}",
            f"{a.avg_context_tokens_second_half:.0f}",
        )

        section("ROUTING QUALITY")
        row(
            "Routing accuracy",
            f"{b.routing_accuracy:.1%}",
            f"{a.routing_accuracy:.1%}",
        )
        row(
            "LOW_MARGIN flags",
            f"{b.flags_low_margin_count}",
            f"{a.flags_low_margin_count}",
        )
        row("OOD flags", f"{b.flags_ood_count}", f"{a.flags_ood_count}")
        row(
            "VECTOR_AMBIGUITY flags",
            f"{b.flags_vector_ambiguity_count}",
            f"{a.flags_vector_ambiguity_count}",
        )
        row(
            "Runner-up capture rate",
            f"{b.runner_up_capture_rate:.1%}",
            f"{a.runner_up_capture_rate:.1%}",
        )

        section("GOVERNANCE")
        row(
            "Conformance violations caught",
            f"{b.conformance_violations_caught}",
            f"{a.conformance_violations_caught}",
        )
        row(
            "Low-confidence hook fires",
            f"{b.low_confidence_hook_fired_count}",
            f"{a.low_confidence_hook_fired_count}",
        )
        row(
            "Conflict hook fires",
            f"{b.conflict_hook_fired_count}",
            f"{a.conflict_hook_fired_count}",
        )
        row(
            "Capability-drift hook fires",
            f"{b.capability_drift_hook_fired_count}",
            f"{a.capability_drift_hook_fired_count}",
        )

        section("TRANSPARENCY / AUDIT")
        row(
            "Decisions w/ explanation",
            f"{b.decisions_with_explanation}",
            f"{a.decisions_with_explanation}",
        )
        row(
            "Decisions w/ alternatives",
            f"{b.decisions_with_alternatives_recorded}",
            f"{a.decisions_with_alternatives_recorded}",
        )
        row(
            "Decisions w/ runner-up reason",
            f"{b.decisions_with_runner_up_reason}",
            f"{a.decisions_with_runner_up_reason}",
        )
        row(
            "Audit completeness score",
            f"{b.audit_completeness_score:.2f}",
            f"{a.audit_completeness_score:.2f}",
        )

        section("RELIABILITY")
        row(
            "Dispatch errors",
            f"{b.dispatch_errors}",
            f"{a.dispatch_errors}",
        )
        row(
            "Dispatch retries",
            f"{b.dispatch_retries}",
            f"{a.dispatch_retries}",
        )
        row("Cold-start ms", f"{b.cold_start_ms:.2f}", f"{a.cold_start_ms:.2f}")
        row("Peak memory MB", f"{b.peak_memory_mb:.2f}", f"{a.peak_memory_mb:.2f}")

        section("DETERMINISM")
        row(
            "Determinism score (3 reruns)",
            f"{b.determinism_score:.2f}",
            f"{a.determinism_score:.2f}",
        )

        section("FEATURE MATRIX")
        row(
            "Routing explanations",
            self._icon(b.has_explanations),
            self._icon(a.has_explanations),
        )
        row(
            "Conformance enforcement",
            self._icon(b.has_conformance),
            self._icon(a.has_conformance),
        )
        row(
            "Governance hooks wired",
            self._icon(b.has_governance_hooks),
            self._icon(a.has_governance_hooks),
        )
        row(
            "Zero coordination tokens",
            self._icon(b.coordination_tokens_total == 0),
            self._icon(a.coordination_tokens_total == 0),
        )
        print("=" * W)


# ---------------------------------------------------------------------------
# Aggregate / headline summary
# ---------------------------------------------------------------------------

def print_aggregate_summary(
    baseline_runs: List[RunMetricsV2],
    a2s_runs: List[RunMetricsV2],
) -> None:
    W = 84
    if not baseline_runs or not a2s_runs:
        return
    sum_b_total = sum(r.total_tokens for r in baseline_runs)
    sum_a_total = sum(r.total_tokens for r in a2s_runs)
    sum_b_coord = sum(r.coordination_tokens_total for r in baseline_runs)
    sum_a_coord = sum(r.coordination_tokens_total for r in a2s_runs)
    sum_b_wall = sum(r.total_wall_time_ms for r in baseline_runs)
    sum_a_wall = sum(r.total_wall_time_ms for r in a2s_runs)
    sum_tasks = sum(r.num_routing_decisions for r in baseline_runs)
    sum_b_conf_caught = sum(r.conformance_violations_caught for r in baseline_runs)
    sum_a_conf_caught = sum(r.conformance_violations_caught for r in a2s_runs)
    sum_b_audit = (
        statistics.fmean([r.audit_completeness_score for r in baseline_runs])
        if baseline_runs else 0.0
    )
    sum_a_audit = (
        statistics.fmean([r.audit_completeness_score for r in a2s_runs])
        if a2s_runs else 0.0
    )

    sum_b_cost_mini = sum(r.cost_usd_gpt4o_mini for r in baseline_runs)
    sum_a_cost_mini = sum(r.cost_usd_gpt4o_mini for r in a2s_runs)
    sum_b_cost_4o = sum(r.cost_usd_gpt4o for r in baseline_runs)
    sum_a_cost_4o = sum(r.cost_usd_gpt4o for r in a2s_runs)
    sum_b_cost_opus = sum(r.cost_usd_claude_opus_4 for r in baseline_runs)
    sum_a_cost_opus = sum(r.cost_usd_claude_opus_4 for r in a2s_runs)

    print()
    print("=" * W)
    print("  AGGREGATE SUMMARY (all scenarios)".center(W))
    print("=" * W)

    col_label = 38
    col_base = 20
    col_a2s = 20

    def row(label: str, bval: str, aval: str) -> None:
        print(f"  {label:<{col_label}} {bval:>{col_base}} {aval:>{col_a2s}}")

    print(
        f"  {'Metric':<{col_label}} "
        f"{'LangGraph':>{col_base}} "
        f"{'agent2society':>{col_a2s}}"
    )
    print("  " + "-" * (W - 4))
    row("Total tokens", f"{sum_b_total:,}", f"{sum_a_total:,}")
    row("  Coordination tokens", f"{sum_b_coord:,}", f"{sum_a_coord:,}")
    row("Total wall time ms", f"{sum_b_wall:.1f}", f"{sum_a_wall:.1f}")
    row("Total tasks routed", f"{sum_tasks}", f"{sum_tasks}")
    row(
        "Cost gpt-4o-mini",
        f"${sum_b_cost_mini:.6f}",
        f"${sum_a_cost_mini:.6f}",
    )
    row("Cost gpt-4o", f"${sum_b_cost_4o:.6f}", f"${sum_a_cost_4o:.6f}")
    row(
        "Cost claude-opus-4",
        f"${sum_b_cost_opus:.6f}",
        f"${sum_a_cost_opus:.6f}",
    )
    row(
        "Conformance violations caught",
        f"{sum_b_conf_caught}",
        f"{sum_a_conf_caught}",
    )
    row(
        "Avg audit completeness",
        f"{sum_b_audit:.2f}",
        f"{sum_a_audit:.2f}",
    )

    # ------------------ Speedup / Savings headline ---------------------
    token_saved = sum_b_total - sum_a_total
    token_saved_pct = (
        (token_saved / sum_b_total * 100.0) if sum_b_total else 0.0
    )
    coord_saved = sum_b_coord - sum_a_coord
    coord_saved_pct = (
        (coord_saved / sum_b_coord * 100.0) if sum_b_coord else 0.0
    )
    wall_saved = sum_b_wall - sum_a_wall
    wall_saved_pct = (
        (wall_saved / sum_b_wall * 100.0) if sum_b_wall else 0.0
    )
    # cost-per-1k-tasks projections (linearly scaled from the totals)
    scale_1k = (1000.0 / sum_tasks) if sum_tasks else 0.0
    cost_save_mini_per_1k = (sum_b_cost_mini - sum_a_cost_mini) * scale_1k
    cost_save_4o_per_1k = (sum_b_cost_4o - sum_a_cost_4o) * scale_1k
    cost_save_opus_per_1k = (sum_b_cost_opus - sum_a_cost_opus) * scale_1k

    print()
    print("=" * W)
    print("  HEADLINE: SPEEDUP / SAVINGS".center(W))
    print("=" * W)
    print(
        f"  Tokens saved across all scenarios : "
        f"{token_saved:>10,}  ({token_saved_pct:5.1f}%)"
    )
    print(
        f"  Coordination tokens eliminated   : "
        f"{coord_saved:>10,}  ({coord_saved_pct:5.1f}%)"
    )
    print(
        f"  Wall-time reduction              : "
        f"{wall_saved:>10.1f} ms ({wall_saved_pct:5.1f}%)"
    )
    print()
    print("  Projected savings per 1,000 routed tasks:")
    print(f"    gpt-4o-mini   :  ${cost_save_mini_per_1k:>10.4f}")
    print(f"    gpt-4o        :  ${cost_save_4o_per_1k:>10.4f}")
    print(f"    claude-opus-4 :  ${cost_save_opus_per_1k:>10.4f}")
    print()
    extra_violations = sum_a_conf_caught - sum_b_conf_caught
    print(
        f"  Extra boundary violations caught by a2s : {extra_violations}"
    )
    print(
        f"  Avg audit-completeness lift             : "
        f"{(sum_a_audit - sum_b_audit):+.2f}"
    )
    print("=" * W)
    print()
