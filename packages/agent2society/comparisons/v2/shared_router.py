"""The fairness fix.

Both runners (LangGraph baseline and agent2society) call the SAME TF-IDF
routing brain to pick (agent, skill). The only difference measured is
the coordination cost surrounding the routing decision -- the routing
itself is byte-for-byte identical, so accuracy is identical.

The baseline wraps this call in a verbose supervisor LLM-style invocation
(big system prompt, growing chat history, ~200 output tokens) that mimics
real supervisor pattern usage. agent2society calls it natively.

Usage:
    route = build_shared_router(AGENT_REGISTRY)
    decision = route("Build a 24-month financial model showing...")
    # decision is a RouteDecision dataclass with agent, skill, score,
    # margin, and the full alternatives list.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from agent2society.card import AgentCard, Skill
from agent2society.graph import CapabilityGraph
from agent2society.router import RouteCandidate, Router


@dataclass
class RouteDecision:
    agent: str
    skill: str
    score: float
    margin: float                                 # top1 - top2
    semantic: float
    tag_overlap: float
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    runner_up_reason: Optional[str] = None        # why top2 was not chosen


SharedRouter = Callable[[str], RouteDecision]


def build_shared_router(agent_registry: Dict[str, Dict[str, Any]]) -> SharedRouter:
    """Build a TF-IDF router pre-loaded with the registry's skills.

    Returns a callable `route(task_text) -> RouteDecision`. Both runners
    invoke this identically; the only thing that differs is the wrapping
    coordination work (LLM tokens for baseline, none for a2s).
    """
    graph = CapabilityGraph()
    for agent_id, agent_info in agent_registry.items():
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
        graph.add_agent(card)

    router = Router(graph)
    # Force index build now so latency measurements don't double-count it.
    router.mark_stale()
    _ = router.route("warmup", top_k=1)

    def route(task_text: str) -> RouteDecision:
        candidates: List[RouteCandidate] = router.route(task_text, top_k=5)
        if not candidates:
            return RouteDecision(
                agent="analysis_agent",
                skill="statistical_analysis",
                score=0.0,
                margin=0.0,
                semantic=0.0,
                tag_overlap=0.0,
                alternatives=[],
                runner_up_reason="no candidates returned by router",
            )
        top = candidates[0]
        runner_up_reason: Optional[str] = None
        margin = 0.0
        if len(candidates) >= 2:
            second = candidates[1]
            margin = top.score - second.score
            runner_up_reason = (
                f"runner-up {second.agent}::{second.skill_id} scored "
                f"{second.score:.3f} (gap {margin:.3f})"
            )
        return RouteDecision(
            agent=top.agent,
            skill=top.skill_id,
            score=top.score,
            margin=margin,
            semantic=top.semantic,
            tag_overlap=top.tag_overlap,
            alternatives=[c.to_dict() for c in candidates],
            runner_up_reason=runner_up_reason,
        )

    return route
