"""Per-run metrics for v3 + reporting.

Captures everything v2 did plus:
  * Real Qwen tokens (input + output) for coordination AND execution
  * Extrapolated cost at each pricing tier
  * Real A2A protocol RTT per dispatch
  * Hallucinated agent routings (baseline LLM picks a non-existent agent)
  * Boundary blocks caught by a2s
  * A2A discovery time (time to fetch all agent cards)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from comparisons.v3.pricing import PRICING, cost


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


@dataclass
class RunMetricsV3:
    runner: str
    scenario_name: str

    # raw per-task records
    task_records: List[Dict[str, Any]] = field(default_factory=list)
    explanations: List[str] = field(default_factory=list)

    # cost (real qwen tokens)
    coordination_input_tokens: int = 0
    coordination_output_tokens: int = 0
    execution_input_tokens: int = 0
    execution_output_tokens: int = 0
    coordination_tokens_total: int = 0
    execution_tokens_total: int = 0
    total_tokens: int = 0
    cost_by_tier: Dict[str, float] = field(default_factory=dict)
    coordination_cost_by_tier: Dict[str, float] = field(default_factory=dict)
    execution_cost_by_tier: Dict[str, float] = field(default_factory=dict)

    # latency
    latency_per_routing_ms: List[float] = field(default_factory=list)
    a2a_rtt_ms: List[float] = field(default_factory=list)
    supervisor_call_ms: List[float] = field(default_factory=list)
    a2s_routing_ms: List[float] = field(default_factory=list)
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    latency_max_ms: float = 0.0
    time_to_first_dispatch_ms: float = 0.0
    total_wall_time_ms: float = 0.0
    throughput_tasks_per_sec: float = 0.0

    # coordination overhead
    coordination_calls_total: int = 0
    coordination_calls_per_task: float = 0.0
    context_tokens_growth: List[int] = field(default_factory=list)
    avg_context_tokens_first_half: float = 0.0
    avg_context_tokens_second_half: float = 0.0

    # routing quality
    flags_low_margin_count: int = 0
    flags_ood_count: int = 0
    flags_vector_ambiguity_count: int = 0
    confidence_distribution: List[float] = field(default_factory=list)
    margin_distribution: List[float] = field(default_factory=list)
    runner_up_capture_rate: float = 0.0

    # governance
    conformance_violations_caught: int = 0
    low_confidence_hook_fired_count: int = 0
    conflict_hook_fired_count: int = 0
    capability_drift_hook_fired_count: int = 0
    human_review_hook_fired_count: int = 0

    # transparency
    decisions_with_explanation: int = 0
    decisions_with_alternatives_recorded: int = 0
    decisions_with_runner_up_reason: int = 0
    audit_completeness_score: float = 0.0

    # reliability
    dispatch_errors: int = 0
    dispatch_retries: int = 0
    hallucinated_agent_routings: int = 0
    cold_start_ms: float = 0.0
    a2a_discovery_ms: float = 0.0

    # routing accuracy
    num_routing_decisions: int = 0
    correct_routings: int = 0
    routing_accuracy: float = 0.0

    # capability flags
    has_explanations: bool = False
    has_conformance: bool = False
    has_governance_hooks: bool = False

    _audit_field_hits: List[int] = field(default_factory=list)

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
        self.cost_by_tier = {
            tier: cost(total_in, total_out, tier) for tier in PRICING
        }
        self.coordination_cost_by_tier = {
            tier: cost(self.coordination_input_tokens, self.coordination_output_tokens, tier)
            for tier in PRICING
        }
        self.execution_cost_by_tier = {
            tier: cost(self.execution_input_tokens, self.execution_output_tokens, tier)
            for tier in PRICING
        }

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

        if self.context_tokens_growth:
            n = len(self.context_tokens_growth)
            mid = max(1, n // 2)
            first = self.context_tokens_growth[:mid]
            second = self.context_tokens_growth[mid:] or first
            self.avg_context_tokens_first_half = statistics.fmean(first) if first else 0.0
            self.avg_context_tokens_second_half = statistics.fmean(second) if second else 0.0

        if self._audit_field_hits:
            self.audit_completeness_score = (
                statistics.fmean(self._audit_field_hits) / 4.0
            )

        if self.num_routing_decisions > 0:
            self.runner_up_capture_rate = (
                self.decisions_with_runner_up_reason / self.num_routing_decisions
            )

    def record_audit(self, *, has_agent: bool, has_reason: bool,
                     has_alternatives: bool, has_runner_up: bool) -> None:
        hits = sum([int(has_agent), int(has_reason),
                    int(has_alternatives), int(has_runner_up)])
        self._audit_field_hits.append(hits)
        if has_reason:
            self.decisions_with_explanation += 1
        if has_alternatives:
            self.decisions_with_alternatives_recorded += 1
        if has_runner_up:
            self.decisions_with_runner_up_reason += 1


class ComparisonReportV3:
    def __init__(self, baseline: RunMetricsV3, a2s: RunMetricsV3) -> None:
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
    def _flag(v: bool) -> str:
        return "YES" if v else " NO"

    def print_report(self) -> None:
        b = self.baseline
        a = self.a2s
        W = 86
        col_label = 38
        col_b = 22
        col_a = 22

        print()
        print("=" * W)
        print(f"  SCENARIO: {b.scenario_name}".center(W))
        print("=" * W)
        print(f"  {'Metric':<{col_label}} {'LangGraph Baseline':>{col_b}} {'agent2society':>{col_a}}")
        print("  " + "-" * (W - 4))

        def row(label, bv, av):
            print(f"  {label:<{col_label}} {bv:>{col_b}} {av:>{col_a}}")

        def section(title):
            print()
            print(f"  [{title}]")
            print("  " + "-" * (W - 4))

        section("COST (real qwen tokens)")
        row("Coordination input tokens",
            f"{b.coordination_input_tokens:,}", f"{a.coordination_input_tokens:,}")
        row("Coordination output tokens",
            f"{b.coordination_output_tokens:,}", f"{a.coordination_output_tokens:,}")
        row("Coordination total",
            f"{b.coordination_tokens_total:,}", f"{a.coordination_tokens_total:,}")
        row("Execution tokens (workers)",
            f"{b.execution_tokens_total:,}", f"{a.execution_tokens_total:,}")
        row("Total tokens",
            f"{b.total_tokens:,}", f"{a.total_tokens:,}")
        row("  Token delta", "", self._saved_pct(b.total_tokens, a.total_tokens))

        section("EXTRAPOLATED COST (USD)")
        for tier in ["gpt-4o-mini", "gpt-4o", "claude-sonnet-4", "claude-opus-4", "qwen2.5:7b-local"]:
            bv = b.cost_by_tier.get(tier, 0.0)
            av = a.cost_by_tier.get(tier, 0.0)
            row(f"  total @ {tier}", f"${bv:.6f}", f"${av:.6f}")
        row("  saved (gpt-4o)", "", self._saved_pct(b.cost_by_tier.get("gpt-4o", 0.0),
                                                    a.cost_by_tier.get("gpt-4o", 0.0)))

        section("LATENCY")
        row("p50 routing ms", f"{b.latency_p50_ms:.2f}", f"{a.latency_p50_ms:.2f}")
        row("p95 routing ms", f"{b.latency_p95_ms:.2f}", f"{a.latency_p95_ms:.2f}")
        row("p99 routing ms", f"{b.latency_p99_ms:.2f}", f"{a.latency_p99_ms:.2f}")
        row("max routing ms", f"{b.latency_max_ms:.2f}", f"{a.latency_max_ms:.2f}")
        if b.a2a_rtt_ms:
            row("mean A2A RTT ms",
                f"{statistics.fmean(b.a2a_rtt_ms):.2f}",
                f"{statistics.fmean(a.a2a_rtt_ms) if a.a2a_rtt_ms else 0.0:.2f}")
        if b.supervisor_call_ms or a.a2s_routing_ms:
            row("mean supervisor / a2s route ms",
                f"{statistics.fmean(b.supervisor_call_ms) if b.supervisor_call_ms else 0.0:.2f}",
                f"{statistics.fmean(a.a2s_routing_ms) if a.a2s_routing_ms else 0.0:.2f}")
        row("Total wall time ms", f"{b.total_wall_time_ms:.1f}", f"{a.total_wall_time_ms:.1f}")
        row("Throughput tasks/sec",
            f"{b.throughput_tasks_per_sec:.2f}", f"{a.throughput_tasks_per_sec:.2f}")

        section("COORDINATION OVERHEAD")
        row("Supervisor / routing calls",
            f"{b.coordination_calls_total}", f"{a.coordination_calls_total}")
        row("Calls per task",
            f"{b.coordination_calls_per_task:.2f}", f"{a.coordination_calls_per_task:.2f}")
        row("Avg context tokens (first half)",
            f"{b.avg_context_tokens_first_half:.0f}",
            f"{a.avg_context_tokens_first_half:.0f}")
        row("Avg context tokens (second half)",
            f"{b.avg_context_tokens_second_half:.0f}",
            f"{a.avg_context_tokens_second_half:.0f}")

        section("ROUTING QUALITY")
        row("Routing accuracy",
            f"{b.routing_accuracy:.1%}", f"{a.routing_accuracy:.1%}")
        row("LOW_MARGIN flags",
            f"{b.flags_low_margin_count}", f"{a.flags_low_margin_count}")
        row("OOD flags", f"{b.flags_ood_count}", f"{a.flags_ood_count}")
        row("VECTOR_AMBIGUITY flags",
            f"{b.flags_vector_ambiguity_count}", f"{a.flags_vector_ambiguity_count}")
        row("Hallucinated agent routings",
            f"{b.hallucinated_agent_routings}", f"{a.hallucinated_agent_routings}")
        row("Runner-up capture rate",
            f"{b.runner_up_capture_rate:.1%}", f"{a.runner_up_capture_rate:.1%}")

        section("GOVERNANCE")
        row("Conformance violations caught",
            f"{b.conformance_violations_caught}", f"{a.conformance_violations_caught}")
        row("Low-confidence hook fires",
            f"{b.low_confidence_hook_fired_count}", f"{a.low_confidence_hook_fired_count}")
        row("Conflict hook fires",
            f"{b.conflict_hook_fired_count}", f"{a.conflict_hook_fired_count}")
        row("Capability-drift hook fires",
            f"{b.capability_drift_hook_fired_count}", f"{a.capability_drift_hook_fired_count}")
        row("Human-review hook fires",
            f"{b.human_review_hook_fired_count}", f"{a.human_review_hook_fired_count}")

        section("TRANSPARENCY / AUDIT")
        row("Decisions w/ explanation",
            f"{b.decisions_with_explanation}", f"{a.decisions_with_explanation}")
        row("Decisions w/ alternatives",
            f"{b.decisions_with_alternatives_recorded}",
            f"{a.decisions_with_alternatives_recorded}")
        row("Decisions w/ runner-up reason",
            f"{b.decisions_with_runner_up_reason}",
            f"{a.decisions_with_runner_up_reason}")
        row("Audit completeness score",
            f"{b.audit_completeness_score:.2f}", f"{a.audit_completeness_score:.2f}")

        section("RELIABILITY / DISCOVERY")
        row("Dispatch errors", f"{b.dispatch_errors}", f"{a.dispatch_errors}")
        row("A2A discovery ms", f"{b.a2a_discovery_ms:.1f}", f"{a.a2a_discovery_ms:.1f}")

        section("FEATURE MATRIX")
        row("Routing explanations",
            self._flag(b.has_explanations), self._flag(a.has_explanations))
        row("Conformance enforcement",
            self._flag(b.has_conformance), self._flag(a.has_conformance))
        row("Governance hooks wired",
            self._flag(b.has_governance_hooks), self._flag(a.has_governance_hooks))
        row("Zero coordination tokens",
            self._flag(b.coordination_tokens_total == 0),
            self._flag(a.coordination_tokens_total == 0))
        print("=" * W)


def print_aggregate_summary(
    baseline_runs: List[RunMetricsV3], a2s_runs: List[RunMetricsV3]
) -> None:
    if not baseline_runs or not a2s_runs:
        return
    W = 86
    sum_b_total = sum(r.total_tokens for r in baseline_runs)
    sum_a_total = sum(r.total_tokens for r in a2s_runs)
    sum_b_coord = sum(r.coordination_tokens_total for r in baseline_runs)
    sum_a_coord = sum(r.coordination_tokens_total for r in a2s_runs)
    sum_b_exec = sum(r.execution_tokens_total for r in baseline_runs)
    sum_a_exec = sum(r.execution_tokens_total for r in a2s_runs)
    sum_b_wall = sum(r.total_wall_time_ms for r in baseline_runs)
    sum_a_wall = sum(r.total_wall_time_ms for r in a2s_runs)
    sum_tasks = sum(r.num_routing_decisions for r in baseline_runs)
    sum_b_conf = sum(r.conformance_violations_caught for r in baseline_runs)
    sum_a_conf = sum(r.conformance_violations_caught for r in a2s_runs)
    sum_b_hall = sum(r.hallucinated_agent_routings for r in baseline_runs)
    sum_a_hall = sum(r.hallucinated_agent_routings for r in a2s_runs)

    sum_b_audit = (statistics.fmean([r.audit_completeness_score for r in baseline_runs])
                   if baseline_runs else 0.0)
    sum_a_audit = (statistics.fmean([r.audit_completeness_score for r in a2s_runs])
                   if a2s_runs else 0.0)

    # Cost-by-tier aggregates
    tiers = ["gpt-4o-mini", "gpt-4o", "claude-sonnet-4", "claude-opus-4", "qwen2.5:7b-local"]
    sum_b_cost = {t: sum(r.cost_by_tier.get(t, 0.0) for r in baseline_runs) for t in tiers}
    sum_a_cost = {t: sum(r.cost_by_tier.get(t, 0.0) for r in a2s_runs) for t in tiers}
    sum_b_cost_coord = {t: sum(r.coordination_cost_by_tier.get(t, 0.0) for r in baseline_runs) for t in tiers}
    sum_a_cost_coord = {t: sum(r.coordination_cost_by_tier.get(t, 0.0) for r in a2s_runs) for t in tiers}

    print()
    print("=" * W)
    print("  AGGREGATE SUMMARY (all scenarios)".center(W))
    print("=" * W)
    col_label = 38
    col_b = 22
    col_a = 22

    def row(label, bv, av):
        print(f"  {label:<{col_label}} {bv:>{col_b}} {av:>{col_a}}")

    print(f"  {'Metric':<{col_label}} {'LangGraph':>{col_b}} {'agent2society':>{col_a}}")
    print("  " + "-" * (W - 4))
    row("Total tokens", f"{sum_b_total:,}", f"{sum_a_total:,}")
    row("  Coordination tokens", f"{sum_b_coord:,}", f"{sum_a_coord:,}")
    row("  Execution tokens (workers)", f"{sum_b_exec:,}", f"{sum_a_exec:,}")
    row("Total wall time ms", f"{sum_b_wall:.1f}", f"{sum_a_wall:.1f}")
    row("Total tasks routed", f"{sum_tasks}", f"{sum_tasks}")
    row("Conformance violations caught",
        f"{sum_b_conf}", f"{sum_a_conf}")
    row("Hallucinated agent routings",
        f"{sum_b_hall}", f"{sum_a_hall}")
    row("Avg audit completeness", f"{sum_b_audit:.2f}", f"{sum_a_audit:.2f}")

    print()
    print("  Total extrapolated $$ by pricing tier:")
    for t in tiers:
        row(f"  total @ {t}",
            f"${sum_b_cost[t]:.6f}", f"${sum_a_cost[t]:.6f}")
    print()
    print("  Coordination-only $$ by pricing tier:")
    for t in tiers:
        row(f"  coord @ {t}",
            f"${sum_b_cost_coord[t]:.6f}", f"${sum_a_cost_coord[t]:.6f}")

    print()
    print("=" * W)
    print("  HEADLINE: SAVINGS".center(W))
    print("=" * W)
    if sum_b_total:
        pct = (sum_b_total - sum_a_total) / sum_b_total * 100.0
        print(f"  Total tokens saved             : {sum_b_total - sum_a_total:>10,}  ({pct:+5.1f}%)")
    if sum_b_coord:
        pct = (sum_b_coord - sum_a_coord) / sum_b_coord * 100.0
        print(f"  Coordination tokens eliminated : {sum_b_coord - sum_a_coord:>10,}  ({pct:+5.1f}%)")
    if sum_b_wall:
        pct = (sum_b_wall - sum_a_wall) / sum_b_wall * 100.0
        print(f"  Wall time saved                : {sum_b_wall - sum_a_wall:>10.0f} ms ({pct:+5.1f}%)")
    print()
    for tier in ["gpt-4o-mini", "gpt-4o", "claude-sonnet-4", "claude-opus-4"]:
        bv = sum_b_cost[tier]
        av = sum_a_cost[tier]
        delta = bv - av
        pct = (delta / bv * 100.0) if bv else 0.0
        print(f"  {tier:<18}: baseline ${bv:>10.4f}   a2s ${av:>10.4f}   "
              f"saved ${delta:>10.4f}  ({pct:+5.1f}%)")
    print()
    extra = sum_a_conf - sum_b_conf
    print(f"  Boundary violations caught (extra by a2s) : {extra}")
    print(f"  Hallucinated routings prevented           : {sum_b_hall - sum_a_hall}")
    print(f"  Audit-completeness lift                   : {(sum_a_audit - sum_b_audit):+.2f}")
    # Headline narrative
    if sum_b_cost.get("gpt-4o", 0.0) > 0:
        bv = sum_b_cost["gpt-4o"]
        av = sum_a_cost["gpt-4o"]
        delta = bv - av
        pct = (delta / bv * 100.0)
        print()
        print(f"  If you had paid GPT-4o for the supervisor across all "
              f"{len(baseline_runs)} scenarios,")
        print(f"  you would have spent ${bv:.4f}. With agent2society, ${av:.4f}.")
        print(f"  Savings: -{pct:.1f}%")
    print("=" * W)
    print()
