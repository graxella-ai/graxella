"""CrewAI adapter.

Wraps a `crewai.Crew` (or anything that quacks like one — has `agents`/`tasks`
and a `kickoff()` method) into an A2A card and local handler.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..card import AgentCard
from ..dispatcher import LocalHandler
from .base import (
    Adapter,
    card_from_attrs,
    text_from_payload,
    wrap_as_response,
)


def _looks_like_crew(obj: Any) -> bool:
    return (
        callable(getattr(obj, "kickoff", None))
        and hasattr(obj, "agents")
        and hasattr(obj, "tasks")
    )


def _skills_from_crew(crew: Any) -> List[Dict[str, Any]]:
    skills: List[Dict[str, Any]] = []
    for member in getattr(crew, "agents", []) or []:
        role = getattr(member, "role", None) or getattr(member, "name", "agent")
        goal = getattr(member, "goal", "") or ""
        backstory = getattr(member, "backstory", "") or ""
        skills.append(
            {
                "id": str(role).lower().replace(" ", "_"),
                "name": str(role),
                "description": f"{goal} {backstory}".strip(),
                "tags": list(getattr(member, "tags", []) or []),
            }
        )
    if not skills:
        skills.append(
            {
                "id": "crew",
                "name": "crew",
                "description": str(getattr(crew, "description", "") or "crewai crew"),
            }
        )
    return skills


class CrewAIAdapter(Adapter):
    name = "crewai"

    def matches(self, obj: Any) -> bool:
        return _looks_like_crew(obj)

    def to_card(self, obj: Any) -> AgentCard:
        name = getattr(obj, "name", None) or "crewai-crew"
        description = getattr(obj, "description", "") or "CrewAI crew"
        return card_from_attrs(
            name=str(name),
            description=str(description),
            skills=_skills_from_crew(obj),
        )

    def to_handler(self, obj: Any) -> LocalHandler:
        def handler(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            text = text_from_payload(payload)
            try:
                result = obj.kickoff(inputs={"task": text})
            except TypeError:
                result = obj.kickoff({"task": text})
            return wrap_as_response(str(result), payload.get("id", "0"))

        return handler
