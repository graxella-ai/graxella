"""agent2society implementation of the benchmark runner.

Uses Society (= Mesh alias) with TF-IDF routing -- zero LLM calls for
coordination. Every routing decision is deterministic, explained, and
conformance-checked.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from agent2society import (
    Handoff,
    Society,
)
from agent2society.card import AgentCard, Skill

from .agents import AGENT_REGISTRY, execute_agent
from .metrics import RunMetrics


# ---------------------------------------------------------------------------
# Local handler factory
# ---------------------------------------------------------------------------

def _make_handler(agent_id: str):
    """Return an A2A-compatible local handler for the given agent.

    The LocalTransport calls handler(url, payload) where payload is the
    A2A JSON-RPC message/send dict.  The handler must return a dict that
    extract_text() can parse.
    """
    def handler(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Extract task text from A2A payload
        try:
            task_text = (
                payload["params"]["message"]["parts"][0]["text"]
            )
        except (KeyError, IndexError, TypeError):
            task_text = str(payload)

        # Extract skill hint if present
        skill_id = "unknown"
        try:
            skill_id = payload["params"]["message"]["metadata"]["agent2society.skill"]
        except (KeyError, TypeError):
            pass

        result_text, _inp, _out = execute_agent(agent_id, skill_id, task_text)
        return {"result": result_text}

    return handler


# ---------------------------------------------------------------------------
# Society builder (created fresh per scenario for clean state)
# ---------------------------------------------------------------------------

def _build_society() -> Society:
    """Build a fresh Society with all 4 agents registered."""
    society = Society()  # defaults to TF-IDF scorer, no embed_fn needed

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
        # Add card to the society (CoW -- builds new router each time)
        society.add(card)

        # Register in-process handler so transport.send() works locally
        society._local.register(f"local://{agent_id}", _make_handler(agent_id))

    # Wire governance hooks (detection-only, non-blocking)
    society.on_low_confidence(lambda exp: None, threshold=0.3)
    society.on_conflict(lambda c: None)

    return society


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def run_scenario(scenario: dict) -> RunMetrics:
    """Run a single scenario using agent2society Society routing."""
    metrics = RunMetrics(
        runner="agent2society",
        scenario_name=scenario["name"],
        has_explanations=True,
        has_conformance=True,
        has_governance_hooks=True,
    )

    society = _build_society()

    start = time.perf_counter()

    for task_info in scenario["tasks"]:
        task_text = task_info["task"]
        expected_agent = task_info["expected_agent"]
        expected_skill = task_info["expected_skill"]

        # Wrap in a Handoff so we can retrieve the explanation by id later
        h = Handoff(task=task_text)

        # Route + dispatch (zero coordination LLM tokens)
        try:
            _result_text = society.run(h)
        except Exception as exc:
            # Surface dispatch errors without crashing the benchmark
            _result_text = f"[ERROR] {exc}"

        # Retrieve routing explanation
        exp = society.explain(h.id)
        exp_rendered = exp.render() if exp is not None else "(no explanation)"
        metrics.explanations.append(exp_rendered)

        # Determine chosen agent from explanation
        chosen_agent = exp.chosen_agent if exp is not None else None
        chosen_skill = exp.chosen_skill if exp is not None else None

        correct = (chosen_agent == expected_agent)

        # Count execution tokens (same mock formula as baseline)
        words = len(task_text.split())
        inp_tok = int(words * 1.33) + 15
        out_tok = words * 8 + 60

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
    # agent2society uses zero LLM tokens for coordination
    metrics.coordination_tokens = 0
    metrics.finalize()
    return metrics
