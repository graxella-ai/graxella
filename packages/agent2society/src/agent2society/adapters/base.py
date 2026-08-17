"""Adapter interface for wrapping native agents into A2A-conformant nodes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .._logging import get_logger
from ..card import AgentCard, parse_card
from ..dispatcher import LocalHandler, extract_text

_log = get_logger(__name__)


@dataclass
class AdaptedAgent:
    card: AgentCard
    handler: LocalHandler


class Adapter:
    """Base class. Subclasses identify a native object and wrap it."""

    name: str = "base"

    def matches(self, obj: Any) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def to_card(self, obj: Any) -> AgentCard:  # pragma: no cover - abstract
        raise NotImplementedError

    def to_handler(self, obj: Any) -> LocalHandler:  # pragma: no cover - abstract
        raise NotImplementedError

    def adapt(self, obj: Any) -> AdaptedAgent:
        return AdaptedAgent(card=self.to_card(obj), handler=self.to_handler(obj))


_REGISTRY: List[Adapter] = []


def register_adapter(adapter: Adapter) -> None:
    _REGISTRY.append(adapter)


def registered_adapters() -> List[Adapter]:
    return list(_REGISTRY)


def adapt(obj: Any) -> Optional[AdaptedAgent]:
    """Find the first registered adapter that matches and return its wrap.

    Adapters' `matches()` and `adapt()` may raise on inputs they were not
    designed to handle (e.g. duck-typed shape checks throwing AttributeError).
    We log and continue rather than abort, so a single buggy third-party
    adapter cannot break registration. Set the "agent2society.adapters"
    logger to DEBUG to see which adapters were skipped and why.
    """
    for ad in _REGISTRY:
        try:
            if ad.matches(obj):
                return ad.adapt(obj)
        except Exception as e:
            _log.debug(
                "adapter %s skipped %s: %s",
                ad.name,
                type(obj).__name__,
                e,
            )
            continue
    return None


# Helpers used by the built-in adapters --------------------------------

def card_from_attrs(
    *,
    name: str,
    description: str,
    skills: List[Dict[str, Any]],
    url: Optional[str] = None,
) -> AgentCard:
    """Build a card dict and parse it via the shared parser."""
    return parse_card(
        {
            "name": name,
            "description": description,
            "url": url or f"local://{name}",
            "version": "0.0.0",
            "skills": skills,
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
        }
    )


def text_from_payload(payload: Dict[str, Any]) -> str:
    """Pull the user text out of an A2A `message/send` payload."""
    params = (payload or {}).get("params") or {}
    msg = params.get("message") or {}
    parts = msg.get("parts") or []
    chunks: List[str] = []
    for p in parts:
        if isinstance(p, dict) and "text" in p:
            chunks.append(str(p["text"]))
    return "\n".join(chunks)


def wrap_as_response(text: str, message_id: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "result": {
            "role": "agent",
            "messageId": message_id,
            "parts": [{"kind": "text", "text": text}],
        },
    }
