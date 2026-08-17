"""Public exceptions for agent2society."""
from __future__ import annotations

from typing import Any, List, Optional


class SocietyError(Exception):
    """Base class for all agent2society errors."""


class AgentCardError(SocietyError):
    """Raised when an A2A Agent Card cannot be parsed or is invalid."""


class NoRouteError(SocietyError):
    """Raised when no agent in the graph can serve a task."""

    def __init__(self, task: str, candidates: Optional[List[Any]] = None):
        super().__init__(f"No agent in the mesh can serve task: {task!r}")
        self.task = task
        self.candidates = candidates or []


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
