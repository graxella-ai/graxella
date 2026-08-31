"""Public exceptions for agent2society."""
from __future__ import annotations

from typing import Any, List, Optional


class SocietyError(Exception):
    """Base class for all agent2society errors."""


class AgentCardError(SocietyError):
    """Raised when an A2A Agent Card cannot be parsed or is invalid."""


class NoRouteError(SocietyError):
    """Raised when no agent in the graph can serve a task.

    The message is self-explaining: it names the closest candidates with
    their scores and says what to do about it, so the first unroutable
    task a developer hits is a diagnosis, not a dead end."""

    def __init__(self, task: str, candidates: Optional[List[Any]] = None):
        self.task = task
        self.candidates = candidates or []
        lines = [f"No agent in the mesh can serve task: {task!r}"]
        shown = [c for c in self.candidates if isinstance(c, dict)][:3]
        if shown:
            lines.append("Closest candidates (all below the routing floor):")
            for c in shown:
                lines.append(
                    f"  - {c.get('agent')} (skill={c.get('skill_id')}, "
                    f"score={c.get('score')})")
        lines.append(
            "Why this happens: with the lexical fallback router the task "
            "must share words with an agent's skill tags. Fixes: run a "
            "local embedding model so semantic routing engages (Ollama + "
            "nomic-embed-text, or pip install sentence-transformers), pass "
            "router='transformer' explicitly, or enrich the agents' skill "
            "descriptions with the words users actually say.")
        super().__init__("\n".join(lines))


class ConformanceViolation(SocietyError):
    """Raised when a dispatch would violate the graph's declared boundaries."""

    def __init__(
        self,
        agent: str,
        skill: Optional[str],
        reason: str,
        task: Optional[str] = None,
    ):
        msg = f"Conformance violation on agent {agent!r}: {reason}"
        if skill:
            msg += f" (skill={skill!r})"
        super().__init__(msg)
        self.agent = agent
        self.skill = skill
        self.reason = reason
        self.task = task


class DispatchError(SocietyError):
    """Raised when dispatch over A2A fails for transport reasons."""
