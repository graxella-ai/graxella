"""agent2society coordinator: Society with TF-IDF routing, real A2A HTTP dispatch.

Workers are discovered via /.well-known/agent-card.json (timed -> a2a_discovery_ms).
Society routes via its built-in router, then dispatches via HttpTransport hitting
the SAME A2A endpoint as the LangGraph baseline.

Conformance: each agent's boundary_deny tags are applied to its skills, so tasks
that mention those tags are blocked. The orchestrator inspects the task text for
the deny tags BEFORE the route call and tags the handoff so the conformance
guard fires.

Governance hooks: all 4 wired with counters.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Dict, List, Optional

from agent2society import (
    AgentCard as A2SAgentCard,
    Handoff,
    HttpTransport,
    Skill,
    Society,
    ConformanceViolation,
    DispatchError,
    GovernanceHooks,
)

from comparisons.v3.agents import AGENTS_V3
from comparisons.v3.metrics_v3 import RunMetricsV3


# --- instrumented transport: captures last reply's metadata + RTT globally ---
_LAST_META: Dict[str, Any] = {}
_LAST_RTT: Dict[str, float] = {"ms": 0.0}


class InstrumentedHttpTransport(HttpTransport):
    """HttpTransport that stashes the last reply's metadata + RTT globally.

    The v0.5.3 Society API doesn't expose the worker's response metadata; we
    capture it here so we can attribute execution tokens correctly.
    """

    def send(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            r = super().send(url, payload)
        finally:
            _LAST_RTT["ms"] = (time.perf_counter() - t0) * 1000.0
        try:
            result = r.get("result", {}) if isinstance(r, dict) else {}
            meta = result.get("metadata") or {}
            _LAST_META.clear()
            _LAST_META.update(meta)
        except Exception:
            _LAST_META.clear()
        return r


# ---------------------------------------------------------------------------
# Agent discovery (real HTTP)
# ---------------------------------------------------------------------------

def discover_agents(agent_urls: Dict[str, str], timeout: float = 5.0) -> Dict[str, Dict[str, Any]]:
    cards = {}
    for name, base in agent_urls.items():
        with urllib.request.urlopen(
            f"{base}/.well-known/agent-card.json", timeout=timeout
        ) as r:
            cards[name] = json.loads(r.read().decode())
    return cards


# ---------------------------------------------------------------------------
# Card translation
# ---------------------------------------------------------------------------

_KEYWORD_BUCKETS = {
    "price":        ["price", "prices", "quote", "data"],
    "ratio":        ["ratio", "ratios", "fundamentals"],
    "volume":       ["volume", "trading"],
    "dividend":     ["dividend", "yield"],
    "news":         ["news", "press", "sentiment", "narrative"],
    "event":        ["event", "earnings", "lawsuit"],
    "dcf":          ["DCF", "valuation"],
    "scenario":     ["scenario", "bear", "base", "bull"],
    "peer":         ["peer", "comparison"],
    "risk":         ["risk", "credit", "drawdown"],
    "regulatory":   ["regulatory", "regime"],
    "esg":          ["ESG", "governance", "environmental"],
    "geopolitical": ["geopolitical", "country", "sanctions"],
    "competitor":   ["competitor", "landscape", "share"],
    "memo":         ["memo", "draft", "letter"],
    "summary":      ["summary", "exec", "brief"],
    "disclosure":   ["disclosure"],
    "mnpi":         ["mnpi", "nonpublic"],
    "restricted":   ["restricted"],
    "report":       ["report", "write", "draft"],
    "compliance":   ["compliance", "audit"],
    "market_data":  ["market data", "price history"],
}


def _tags_from_desc(desc: str) -> List[str]:
    out = []
    desc_l = desc.lower()
    for key, words in _KEYWORD_BUCKETS.items():
        for w in words:
            if w.lower() in desc_l:
                out.append(key)
                break
    return out


def _build_a2s_card(name: str, base_url: str, card_dict: Dict[str, Any]) -> A2SAgentCard:
    cfg = AGENTS_V3[name]
    skills = [
        Skill(
            id=f"{name}.{sid}",
            name=sid,
            description=desc,
            tags=[name, sid, *_tags_from_desc(desc)],
        )
        for sid, desc in cfg["skills"]
    ]
    return A2SAgentCard(
        name=name,
        url=base_url + ("" if base_url.endswith("/") else "/"),
        description=cfg["description"],
        version="1.0.0",
        skills=skills,
    )


def _detect_deny_tags(task: str, agent_deny: Dict[str, List[str]]) -> List[str]:
    """Detect deny tags whose name appears literally in the task text."""
    triggers: List[str] = []
    task_l = task.lower()
    all_deny = set()
    for v in agent_deny.values():
        for t in v:
            all_deny.add(t)
    for tag in all_deny:
        if tag.lower() in task_l:
            triggers.append(tag)
    return triggers


# ---------------------------------------------------------------------------
# Main scenario runner
# ---------------------------------------------------------------------------

def run_a2s_scenario(
    scenario: Dict[str, Any],
    agent_urls: Dict[str, str],
    *,
    cards: Optional[Dict[str, Dict[str, Any]]] = None,
    metrics: Optional[RunMetricsV3] = None,
) -> RunMetricsV3:
    metrics = metrics or RunMetricsV3(runner="agent2society", scenario_name=scenario["name"])
    metrics.has_explanations = True
    metrics.has_conformance = True
    metrics.has_governance_hooks = True

    # Discovery (one-time per scenario; orchestrator can pass `cards` to amortise)
    t0 = time.perf_counter()
    if cards is None:
        cards = discover_agents(agent_urls)
    metrics.a2a_discovery_ms = (time.perf_counter() - t0) * 1000.0

    # Reset capture globals (per-scenario)
    _LAST_META.clear()
    _LAST_RTT["ms"] = 0.0

    # Governance hooks with counters
    def on_low_confidence(rec):
        metrics.low_confidence_hook_fired_count += 1
    def on_low_margin(rec):
        # tracked via flags too; the dedicated hook count is separate.
        pass
    def on_conflict(rec):
        metrics.conflict_hook_fired_count += 1
    def on_drift(rec):
        metrics.capability_drift_hook_fired_count += 1
    def on_human_review(rec):
        metrics.human_review_hook_fired_count += 1

    society = Society(
        transport=InstrumentedHttpTransport(timeout=120.0),
        min_score=0.02,
        strict=False,
    )
    society.on_low_confidence(on_low_confidence)
    society.on_low_margin(on_low_margin)
    society.on_conflict(on_conflict)
    society.on_capability_drift(on_drift)
    society.on_human_review(on_human_review)

    # Build agent_deny lookup + register agents/boundaries
    agent_deny: Dict[str, List[str]] = {}
    for name in agent_urls:
        deny = (AGENTS_V3[name].get("boundary_deny") or [])
        agent_deny[name] = list(deny)

    for name, base in agent_urls.items():
        a2s_card = _build_a2s_card(name, base, cards[name])
        society.add(a2s_card)
        if agent_deny[name]:
            society.boundary(name, deny=agent_deny[name])

    # Process tasks
    done: List[Dict[str, Any]] = []
    t_start = time.perf_counter()

    for i, t in enumerate(scenario["tasks"]):
        deps = t.get("depends_on", []) or []
        dep_ctx = []
        for di in deps:
            for d in done:
                if d["task_index"] == di:
                    dep_ctx.append(d.get("answer", "")[:200])
        ctx_block = " ".join(dep_ctx)
        # Route on the ORIGINAL short task text (so context-bloat doesn't poison TF-IDF).
        route_task = t["task"]
        # But carry the context as a message payload for the worker.
        full_task = route_task
        if ctx_block:
            full_task = f"{route_task}\n\nPrior context: {ctx_block}"

        # Detect deny-tag triggers (this is the orchestrator-side guard).
        triggered_deny = _detect_deny_tags(route_task, agent_deny)
        handoff = Handoff(task=route_task, metadata={"full_text": full_task})

        metrics.context_tokens_growth.append(len(full_task) // 4)

        chosen_agent = ""
        raw_skill = ""
        answer = ""
        blocked = False
        explanation = None
        dispatch_err = None

        # Reset per-call meta capture
        _LAST_META.clear()
        _LAST_RTT["ms"] = 0.0

        t_route_start = time.perf_counter()
        try:
            answer = society.run(
                handoff,
                tags=triggered_deny if triggered_deny else None,
            )
        except ConformanceViolation as e:
            blocked = True
            metrics.conformance_violations_caught += 1
            answer = ""
            dispatch_err = f"blocked: {e}"
        except DispatchError as e:
            dispatch_err = str(e)
            metrics.dispatch_errors += 1
        except Exception as e:
            dispatch_err = f"{type(e).__name__}: {e}"
            metrics.dispatch_errors += 1
        route_elapsed_ms = (time.perf_counter() - t_route_start) * 1000.0

        explanation = society.last_explanation()
        metrics.a2s_routing_ms.append(route_elapsed_ms)
        metrics.latency_per_routing_ms.append(route_elapsed_ms)
        metrics.coordination_calls_total += 1
        metrics.num_routing_decisions += 1

        if explanation:
            chosen_agent = explanation.chosen_agent or ""
            chosen_skill_full = explanation.chosen_skill or ""
            if chosen_skill_full and "." in chosen_skill_full:
                _, _, raw_skill = chosen_skill_full.partition(".")
            else:
                raw_skill = chosen_skill_full
            for f in (explanation.flags or ()):
                if f == "LOW_MARGIN":
                    metrics.flags_low_margin_count += 1
                elif f == "OOD":
                    metrics.flags_ood_count += 1
                elif f == "VECTOR_AMBIGUITY":
                    metrics.flags_vector_ambiguity_count += 1
            metrics.confidence_distribution.append(explanation.confidence)
            metrics.margin_distribution.append(explanation.margin)
            metrics.record_audit(
                has_agent=bool(chosen_agent),
                has_reason=bool(explanation.rationale),
                has_alternatives=bool(explanation.alternatives),
                has_runner_up=len(explanation.alternatives) >= 1,
            )
            # If the route was blocked, count the explanation's blocked_reason too
            if explanation.blocked_reason and not blocked:
                # blocked_reason on explanation but not raised because strict=False
                metrics.conformance_violations_caught += 1
                blocked = True
        else:
            metrics.record_audit(
                has_agent=False, has_reason=False,
                has_alternatives=False, has_runner_up=False,
            )

        expected = t.get("expected_agent")
        if expected and chosen_agent == expected:
            metrics.correct_routings += 1

        # Worker token capture from the instrumented transport
        meta = _LAST_META.copy()
        in_tok = int(float(meta.get("agent_input_tokens", 0) or 0))
        out_tok = int(float(meta.get("agent_output_tokens", 0) or 0))
        metrics.execution_input_tokens += in_tok
        metrics.execution_output_tokens += out_tok

        # agent2society uses 0 LLM tokens for routing (TF-IDF); coordination_*_tokens stay at 0.

        done.append({
            "task_index": i,
            "task": t["task"],
            "agent": chosen_agent,
            "skill": raw_skill,
            "answer": answer,
            "blocked": blocked,
            "error": dispatch_err,
            "rtt_ms": _LAST_RTT["ms"],
        })
        metrics.a2a_rtt_ms.append(_LAST_RTT["ms"])
        metrics.task_records.append({
            "task_index": i,
            "task": t["task"],
            "agent": chosen_agent,
            "skill": raw_skill,
            "rationale": (explanation.rationale if explanation else None),
            "flags": (list(explanation.flags) if explanation else []),
            "confidence": (explanation.confidence if explanation else 0.0),
            "margin": (explanation.margin if explanation else 0.0),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "blocked": blocked,
            "error": dispatch_err,
        })
        if explanation and explanation.rationale:
            metrics.explanations.append(explanation.rationale)

    metrics.total_wall_time_ms = (time.perf_counter() - t_start) * 1000.0
    metrics.finalize()
    return metrics
