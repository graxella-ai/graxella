"""LangGraph adapter.

Wraps a compiled LangGraph (anything with `.invoke(state)` and a `.nodes`
mapping) into an A2A card and local handler.
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


def _looks_like_langgraph(obj: Any) -> bool:
    return (
        callable(getattr(obj, "invoke", None))
        and (hasattr(obj, "nodes") or hasattr(obj, "get_graph"))
    )


def _skills_from_graph(graph: Any) -> List[Dict[str, Any]]:
    nodes: Dict[str, Any] = getattr(graph, "nodes", {}) or {}
    skills: List[Dict[str, Any]] = []
    for node_id in nodes:
        if node_id in ("__start__", "__end__"):
            continue
        skills.append(
            {
                "id": str(node_id),
                "name": str(node_id),
                "description": f"LangGraph node {node_id}",
            }
        )
    if not skills:
        skills.append(
            {
                "id": "graph",
                "name": "graph",
                "description": "LangGraph compiled graph",
            }
        )
    return skills


class LangGraphAdapter(Adapter):
    name = "langgraph"

    def matches(self, obj: Any) -> bool:
        return _looks_like_langgraph(obj)

    def to_card(self, obj: Any) -> AgentCard:
        name = getattr(obj, "name", None) or "langgraph"
        return card_from_attrs(
            name=str(name),
            description=str(getattr(obj, "description", "") or "LangGraph graph"),
            skills=_skills_from_graph(obj),
        )

    def to_handler(self, obj: Any) -> LocalHandler:
        def handler(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            text = text_from_payload(payload)
            state = {"input": text, "messages": [{"role": "user", "content": text}]}
            result = obj.invoke(state)
            if isinstance(result, dict):
                # Try common output keys first.
                for key in ("output", "result", "final", "messages"):
                    if key in result:
                        result = result[key]
                        break
            return wrap_as_response(str(result), payload.get("id", "0"))

        return handler
