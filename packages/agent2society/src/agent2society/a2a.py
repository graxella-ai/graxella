"""agent2society.a2a — A2A wire-format bridge (graxella task 3-5).

AgentCard was A2A-shaped from birth; this module pins the mapping to the
A2A v1 JSON wire format (camelCase) so meshes can EMIT cards other A2A
runtimes consume, and CONSUME cards they publish. ``load_card`` already
tolerates both key styles on the way in; ``to_a2a_dict`` is the exact
emitter for the way out.
"""
from __future__ import annotations

from typing import Any, Dict

from .card import AgentCard, load_card

A2A_PROTOCOL_VERSION = "1.0"


def to_a2a_dict(card: AgentCard) -> Dict[str, Any]:
    """Render an AgentCard as an A2A v1 agent-card JSON object."""
    return {
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "name": card.name,
        "url": card.url,
        "description": card.description,
        "version": card.version,
        "capabilities": dict(card.capabilities),
        "provider": dict(card.provider),
        "defaultInputModes": list(card.default_input_modes),
        "defaultOutputModes": list(card.default_output_modes),
        "skills": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "tags": list(s.tags),
                "examples": list(s.examples),
                "inputModes": list(s.input_modes),
                "outputModes": list(s.output_modes),
            }
            for s in card.skills
        ],
    }


def from_a2a_dict(payload: Dict[str, Any]) -> AgentCard:
    """Parse an A2A agent-card JSON object (camelCase or snake_case)."""
    return load_card(dict(payload))


__all__ = ["to_a2a_dict", "from_a2a_dict", "A2A_PROTOCOL_VERSION"]
