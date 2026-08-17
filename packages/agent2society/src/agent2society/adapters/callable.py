"""Generic adapter: any callable, or any object with `.run`/`.invoke`/`.kickoff`.

Authors who want a quick mesh entry without committing to a framework
adapter can pass either a plain function or an object exposing one of the
common method names. The card must be supplied as attributes on the object
(`name`, `description`, `skills`) or via a `to_a2a_card()` method.
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


def _resolve_runner(obj: Any):
    for attr in ("run", "invoke", "kickoff", "__call__"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            return fn
    return None


def _resolve_card(obj: Any) -> AgentCard:
    if hasattr(obj, "to_a2a_card"):
        card = obj.to_a2a_card()
        if isinstance(card, AgentCard):
            return card
        if isinstance(card, dict):
            from ..card import parse_card

            return parse_card(card)
    name = getattr(obj, "name", None) or getattr(obj, "__name__", None) or "agent"
    description = getattr(obj, "description", "") or (obj.__doc__ or "")
    skills: List[Dict[str, Any]] = list(getattr(obj, "skills", []) or [])
    if not skills:
        skills = [
            {
                "id": "default",
                "name": "default",
                "description": description or name,
                "tags": list(getattr(obj, "tags", []) or []),
            }
        ]
    return card_from_attrs(
        name=str(name),
        description=str(description),
        skills=skills,
    )


class CallableAdapter(Adapter):
    """Catch-all adapter. Last in the registry."""

    name = "callable"

    def matches(self, obj: Any) -> bool:
        return _resolve_runner(obj) is not None

    def to_card(self, obj: Any) -> AgentCard:
        return _resolve_card(obj)

    def to_handler(self, obj: Any) -> LocalHandler:
        runner = _resolve_runner(obj)
        if runner is None:
            raise TypeError(f"object {obj!r} is not callable")

        def handler(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            text = text_from_payload(payload)
            try:
                out = runner(text)
            except TypeError:
                out = runner({"task": text})
            return wrap_as_response(str(out), payload.get("id", "0"))

        return handler
