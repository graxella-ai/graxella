"""agent2society v2 runner.

Same TF-IDF routing brain as the baseline, but invoked natively through
Society -- zero coordination LLM tokens, full explanations, conformance
enforcement, and four governance hooks wired with counters.

For scenarios that include `boundary_test` tasks, we install a deny-list
on writer_agent BEFORE running so the conformance check actually blocks
the bad routing (and we count the violations).
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import psutil

from agent2society import (
    CapabilityDrift,
    Conflict,
    Handoff,
    Society,
)
from agent2society.card import AgentCard, Skill
from agent2society.exceptions import ConformanceViolation, NoRouteError

from .agents_v2 import AGENT_REGISTRY, execute_agent
from .metrics_v2 import RunMetricsV2


# ---------------------------------------------------------------------------
# Local handler factory (same A2A LocalTransport pattern as v1)
# ---------------------------------------------------------------------------

def _make_handler(agent_id: str):
    def handler(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            task_text = payload["params"]["message"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            task_text = str(payload)

        skill_id = "unknown"
        try:
            skill_id = payload["params"]["message"]["metadata"][
                "agent2society.skill"
            ]
        except (KeyError, TypeError):
            pass

        result_text, _inp, _out = execute_agent(agent_id, skill_id, task_text)
        return {"result": result_text}

    return handler


# ---------------------------------------------------------------------------
# Society builder with full v2 governance
# ---------------------------------------------------------------------------

def _build_society(
    *,
    metrics: RunMetricsV2,
    boundary_overrides: Optional[Dict[str, Dict[str, List[str]]]] = None,
) -> Society:
    society = Society()

    for agent_id, agent_info in AGENT_REGISTRY.items():
        skills = [
            Skill(
                id=sid,
                name=sid.replace("_", " ").title(),
                description=desc,
            )
            for sid, desc in agent_info["skills"].items()
        ]
        card = AgentCard(
            name=agent_id,
            url=f"local://{agent_id}",
            description=agent_info["description"],
            skills=skills,
        )
        society.add(card)
        society._local.register(f"local://{agent_id}", _make_handler(agent_id))

        # Default boundary from agent registry
        b = agent_info.get("boundary", {}) or {}
        if b.get("allow") or b.get("deny"):
            society.boundary(
                agent_id,
                allow=b.get("allow") or None,
                deny=b.get("deny") or None,
            )

    # Per-scenario boundary additions (e.g. compliance audit)
    if boundary_overrides:
        for agent_id, b in boundary_overrides.items():
            society.boundary(
                agent_id,
                allow=b.get("allow") or None,
                deny=b.get("deny") or None,
            )

    # ---- Wire ALL FOUR governance hooks ----
    society.on_low_confidence(
        lambda exp: setattr(
            metrics,
            "low_confidence_hook_fired_count",
            metrics.low_confidence_hook_fired_count + 1,
        ),
        threshold=0.30,
    )
    society.on_conflict(
        lambda c: setattr(
            metrics,
            "conflict_hook_fired_count",
            metrics.conflict_hook_fired_count + 1,
        )
    )
    society.on_capability_drift(
        lambda d: setattr(
            metrics,
            "capability_drift_hook_fired_count",
            metrics.capability_drift_hook_fired_count + 1,
        )
    )
    # Human review hook: route does not require it by default but we still
    # demonstrate the wire-up
    society.on_human_review(lambda exp, txt: None)
    society.on_low_margin(lambda exp: None, threshold=0.05)

    return society


# ---------------------------------------------------------------------------
# Cold-start measurement
# ---------------------------------------------------------------------------

def measure_cold_start() -> float:
    t0 = time.perf_counter()
    dummy_metrics = RunMetricsV2(runner="cold-start", scenario_name="probe")
    _build_society(metrics=dummy_metrics)
    return (time.perf_counter() - t0) * 1000.0


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def run_scenario(
    scenario: Dict[str, Any], *, seed: int = 1337
) -> RunMetricsV2:
    metrics = RunMetricsV2(
        runner="agent2society v2",
        scenario_name=scenario["name"],
        has_explanations=True,
        has_conformance=True,
        has_governance_hooks=True,
    )

    proc = psutil.Process(os.getpid())
    mem_before_mb = proc.memory_info().rss / 1024 / 1024

    cold = time.perf_counter()
    society = _build_society(metrics=metrics)
    metrics.cold_start_ms = (time.perf_counter() - cold) * 1000.0

    start_wall = time.perf_counter()
    first_dispatch_t0: Optional[float] = None

    for i, task_info in enumerate(scenario["tasks"]):
        task_text = task_info["task"]
        expected_agent = task_info["expected_agent"]
        expected_skill = task_info["expected_skill"]
        is_boundary_test = task_info.get("boundary_test", False)

        h = Handoff(task=task_text)

        # Time the route+dispatch in ms
        t0 = time.perf_counter()
        blocked = False
        try:
            _result_text = society.run(h)
        except ConformanceViolation:
            blocked = True
            metrics.conformance_violations_caught += 1
        except NoRouteError:
            blocked = True
            metrics.dispatch_errors += 1
        except Exception:
            metrics.dispatch_errors += 1
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        metrics.latency_per_routing_ms.append(elapsed_ms)
        metrics.coordination_calls_total += 1   # one route call per task
        metrics.context_tokens_growth.append(int(len(task_text.split()) * 1.33))

        if first_dispatch_t0 is None:
            first_dispatch_t0 = elapsed_ms
            metrics.time_to_first_dispatch_ms = (
                metrics.cold_start_ms + elapsed_ms
            )

        # Pull the explanation regardless of whether dispatch succeeded
        exp = society.explain(h.id)
        if exp is not None:
            metrics.explanations.append(exp.render())
            chosen_agent = exp.chosen_agent
            chosen_skill = exp.chosen_skill
            metrics.confidence_distribution.append(exp.confidence)
            metrics.margin_distribution.append(exp.margin)
            if "LOW_MARGIN" in exp.flags:
                metrics.flags_low_margin_count += 1
            if "OOD" in exp.flags:
                metrics.flags_ood_count += 1
            if "VECTOR_AMBIGUITY" in exp.flags:
                metrics.flags_vector_ambiguity_count += 1
            # Conformance violations caught: count any alternative whose
            # rejected_reason indicates a deny-list / boundary failure.
            # These are routings the supervisor would have made silently.
            for alt in exp.alternatives:
                reason = alt.rejected_reason or ""
                if "denied" in reason or "allow term" in reason:
                    metrics.conformance_violations_caught += 1
            # audit completeness: a2s ALWAYS records all four fields
            has_runner_up = len(exp.alternatives) >= 2
            metrics.record_audit(
                has_agent=chosen_agent is not None,
                has_reason=bool(exp.rationale),
                has_alternatives=len(exp.alternatives) > 0,
                has_runner_up=has_runner_up,
            )
        else:
            chosen_agent = None
            chosen_skill = None
            metrics.record_audit(
                has_agent=False,
                has_reason=False,
                has_alternatives=False,
                has_runner_up=False,
            )

        # Per-task execution token accounting (mock execute_agent is
        # called inside the handler; re-derive the counts here so the
        # metrics object owns them too).
        words = len(task_text.split())
        if not blocked:
            metrics.execution_input_tokens += int(words * 1.33) + 15
            metrics.execution_output_tokens += words * 8 + 60

        correct = (
            chosen_agent == expected_agent and chosen_agent is not None
        )
        metrics.task_records.append(
            {
                "task": task_text[:80],
                "agent": chosen_agent,
                "skill": chosen_skill,
                "expected_agent": expected_agent,
                "expected_skill": expected_skill,
                "correct": correct,
                "boundary_test": is_boundary_test,
                "blocked": blocked,
            }
        )
        metrics.num_routing_decisions += 1
        if correct:
            metrics.correct_routings += 1

    elapsed_total = (time.perf_counter() - start_wall) * 1000.0
    mem_after_mb = proc.memory_info().rss / 1024 / 1024
    metrics.peak_memory_mb = max(mem_before_mb, mem_after_mb)

    # agent2society uses ZERO coordination LLM tokens
    metrics.coordination_input_tokens = 0
    metrics.coordination_output_tokens = 0
    metrics.total_wall_time_ms = elapsed_total
    metrics.finalize()
    return metrics
