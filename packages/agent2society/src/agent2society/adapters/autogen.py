"""AutoGen adapter.

Wraps either a single AutoGen `ConversableAgent` or a `GroupChat`/`GroupChatManager`
into an A2A card and a local handler.
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


def _looks_like_autogen(obj: Any) -> bool:
    if callable(getattr(obj, "initiate_chat", None)):
        return True
    if callable(getattr(obj, "generate_reply", None)):
        return True
    return False


def _skills_from_autogen(obj: Any) -> List[Dict[str, Any]]:
    agents = getattr(obj, "agents", None) or getattr(
        getattr(obj, "groupchat", None), "agents", None
    )
    skills: List[Dict[str, Any]] = []
    if agents:
        for a in agents:
            name = getattr(a, "name", "agent")
            desc = getattr(a, "description", "") or getattr(a, "system_message", "")
            skills.append(
                {
                    "id": str(name).lower().replace(" ", "_"),
                    "name": str(name),
                    "description": str(desc),
                }
            )
    if not skills:
        skills.append(
            {
                "id": "chat",
                "name": "chat",
                "description": str(getattr(obj, "description", "") or "AutoGen agent"),
            }
        )
    return skills


class AutogenAdapter(Adapter):
    name = "autogen"

    def matches(self, obj: Any) -> bool:
        return _looks_like_autogen(obj)

    def to_card(self, obj: Any) -> AgentCard:
        name = getattr(obj, "name", None) or "autogen"
        description = (
            getattr(obj, "description", None)
            or getattr(obj, "system_message", "")
            or "AutoGen agent"
        )
        return card_from_attrs(
            name=str(name),
            description=str(description),
            skills=_skills_from_autogen(obj),
        )

    def to_handler(self, obj: Any) -> LocalHandler:
        def handler(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            text = text_from_payload(payload)
            if callable(getattr(obj, "generate_reply", None)):
                reply = obj.generate_reply(
                    messages=[{"role": "user", "content": text}]
                )
            else:
                reply = obj.initiate_chat(message=text)
            return wrap_as_response(str(reply), payload.get("id", "0"))

        return handler
