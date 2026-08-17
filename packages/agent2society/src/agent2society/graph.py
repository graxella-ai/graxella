"""Capability graph built deterministically from A2A Agent Cards.

Nodes: agents and skills.
Edges: agent --declares--> skill, agent --depends_on--> agent (declared).

No model is in the loop here — this is plain typed parsing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .card import AgentCard


@dataclass
class Boundary:
    """Declared ownership boundary for an agent.

    `allow`/`deny` are matched against task content (substring, case-insensitive)
    and against a task's declared tags. `deny` always wins over `allow`.
    """

    allow: List[str] = field(default_factory=list)
    deny: List[str] = field(default_factory=list)

    def merge(self, other: "Boundary") -> "Boundary":
        return Boundary(
            allow=list(dict.fromkeys(self.allow + other.allow)),
            deny=list(dict.fromkeys(self.deny + other.deny)),
        )


@dataclass
class AgentNode:
    card: AgentCard
    boundary: Boundary = field(default_factory=Boundary)
    depends_on: Set[str] = field(default_factory=set)

    @property
    def name(self) -> str:
        return self.card.name

    @property
    def url(self) -> str:
        return self.card.url

    @property
    def skill_ids(self) -> List[str]:
        return [s.id for s in self.card.skills]


class CapabilityGraph:
    """Typed graph of agents and their declared skills."""

    def __init__(self) -> None:
        self._agents: Dict[str, AgentNode] = {}

    # ---- mutation ------------------------------------------------------
    def add_agent(self, card: AgentCard) -> AgentNode:
        if card.name in self._agents:
            # Replace card but keep boundary additions from before.
            existing = self._agents[card.name]
            existing.card = card
            return existing
        node = AgentNode(card=card)
        self._agents[card.name] = node
        return node

    def set_boundary(
        self,
        agent: str,
        *,
        allow: Optional[Iterable[str]] = None,
        deny: Optional[Iterable[str]] = None,
    ) -> None:
        node = self.require(agent)
        added = Boundary(allow=list(allow or []), deny=list(deny or []))
        node.boundary = node.boundary.merge(added)

    def add_dependency(self, from_agent: str, to_agent: str) -> None:
        src = self.require(from_agent)
        self.require(to_agent)
        src.depends_on.add(to_agent)

    # ---- reads ---------------------------------------------------------
    def require(self, name: str) -> AgentNode:
        if name not in self._agents:
            raise KeyError(f"Unknown agent {name!r}")
        return self._agents[name]

    def get(self, name: str) -> Optional[AgentNode]:
        return self._agents.get(name)

    def agents(self) -> List[AgentNode]:
        return list(self._agents.values())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._agents

    def __len__(self) -> int:
        return len(self._agents)

    # ---- snapshots -----------------------------------------------------
    def edges(self) -> List[Tuple[str, str, str]]:
        """Return (source, relation, target) edges. Useful for visualisation."""
        out: List[Tuple[str, str, str]] = []
        for node in self._agents.values():
            for s in node.card.skills:
                out.append((node.name, "declares", s.id))
            for dep in sorted(node.depends_on):
                out.append((node.name, "depends_on", dep))
        return out
