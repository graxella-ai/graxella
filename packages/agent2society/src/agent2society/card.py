"""A2A Agent Card parsing.

Implements the subset of the A2A (Agent-to-Agent) Agent Card schema that
agent2society routes against. The card is *deterministic data*: no LLM is involved
in parsing or interpretation.

Spec reference: https://a2aproject.github.io/A2A/specification/  (Agent Card)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .exceptions import AgentCardError


@dataclass(frozen=True)
class Skill:
    """A single skill an agent declares it can perform."""

    id: str
    name: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    input_modes: List[str] = field(default_factory=list)
    output_modes: List[str] = field(default_factory=list)

    def search_text(self) -> str:
        parts = [self.id, self.name, self.description]
        parts.extend(self.tags)
        parts.extend(self.examples)
        return " ".join(p for p in parts if p)


@dataclass(frozen=True)
class SelfAssessment:
    """An agent's self-declared limits and confidence shape.

    The skills list says what an agent *can* do. SelfAssessment says where
    that capability ends -- what the agent knows it cannot do, when it
    should escalate, and the model it uses to compute the confidence it
    reports. This is the field that makes the agent legible to humans and
    to downstream agents, not just routable.

    All fields are optional. Cards without a `selfAssessment` block still
    parse; `mesh.explain()` will note "no self-assessment declared", which
    is itself useful signal.
    """

    confidence_model: str = "none"
    known_limitations: List[str] = field(default_factory=list)
    out_of_scope: List[str] = field(default_factory=list)
    escalate_when: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            self.confidence_model in ("", "none")
            and not self.known_limitations
            and not self.out_of_scope
            and not self.escalate_when
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "confidence_model": self.confidence_model,
            "known_limitations": list(self.known_limitations),
            "out_of_scope": list(self.out_of_scope),
            "escalate_when": list(self.escalate_when),
        }


@dataclass
class AgentCard:
    """Parsed A2A Agent Card.

    Only the fields agent2society routes against are first-class. The raw card is
    preserved in `raw` for adapters that need provider-specific fields.
    """

    name: str
    url: str
    description: str = ""
    version: str = "0.0.0"
    skills: List[Skill] = field(default_factory=list)
    default_input_modes: List[str] = field(default_factory=list)
    default_output_modes: List[str] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    provider: Dict[str, Any] = field(default_factory=dict)
    self_assessment: Optional[SelfAssessment] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def skill(self, skill_id: str) -> Optional[Skill]:
        for s in self.skills:
            if s.id == skill_id:
                return s
        return None

    def declares(self, skill_id: str) -> bool:
        return self.skill(skill_id) is not None


def parse_card(data: Dict[str, Any]) -> AgentCard:
    """Parse a dict that follows the A2A Agent Card schema.

    Tolerant of both camelCase (spec) and snake_case keys.
    """
    if not isinstance(data, dict):
        raise AgentCardError(f"Agent card must be a dict, got {type(data).__name__}")

    def pick(*keys: str, default: Any = None) -> Any:
        for k in keys:
            if k in data and data[k] is not None:
                return data[k]
        return default

    name = pick("name")
    url = pick("url", "endpoint")
    if not name or not url:
        raise AgentCardError(
            f"Agent card missing required fields: name={name!r}, url={url!r}"
        )

    skills_raw: Sequence[Dict[str, Any]] = pick("skills", default=[]) or []
    skills: List[Skill] = []
    for s in skills_raw:
        if not isinstance(s, dict):
            raise AgentCardError(f"Skill must be a dict, got {type(s).__name__}")
        sid = s.get("id") or s.get("name")
        if not sid:
            raise AgentCardError(f"Skill missing id/name: {s!r}")
        skills.append(
            Skill(
                id=str(sid),
                name=str(s.get("name", sid)),
                description=str(s.get("description", "") or ""),
                tags=list(s.get("tags", []) or []),
                examples=list(s.get("examples", []) or []),
                input_modes=list(
                    s.get("inputModes", s.get("input_modes", [])) or []
                ),
                output_modes=list(
                    s.get("outputModes", s.get("output_modes", [])) or []
                ),
            )
        )

    sa_raw = pick("selfAssessment", "self_assessment", default=None)
    self_assessment: Optional[SelfAssessment] = None
    if isinstance(sa_raw, dict):
        self_assessment = SelfAssessment(
            confidence_model=str(
                sa_raw.get("confidenceModel", sa_raw.get("confidence_model", "none"))
                or "none"
            ),
            known_limitations=list(
                sa_raw.get(
                    "knownLimitations", sa_raw.get("known_limitations", [])
                )
                or []
            ),
            out_of_scope=list(
                sa_raw.get("outOfScope", sa_raw.get("out_of_scope", [])) or []
            ),
            escalate_when=list(
                sa_raw.get("escalateWhen", sa_raw.get("escalate_when", [])) or []
            ),
        )

    return AgentCard(
        name=str(name),
        url=str(url),
        description=str(pick("description", default="") or ""),
        version=str(pick("version", default="0.0.0") or "0.0.0"),
        skills=skills,
        default_input_modes=list(
            pick("defaultInputModes", "default_input_modes", default=[]) or []
        ),
        default_output_modes=list(
            pick("defaultOutputModes", "default_output_modes", default=[]) or []
        ),
        capabilities=dict(pick("capabilities", default={}) or {}),
        provider=dict(pick("provider", default={}) or {}),
        self_assessment=self_assessment,
        raw=dict(data),
    )


def load_card_from_path(path: str) -> AgentCard:
    """Load and parse an Agent Card JSON file from disk."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise AgentCardError(f"Could not load card at {path!r}: {e}") from e
    return parse_card(data)


def load_card_from_url(url: str, *, timeout: float = 10.0) -> AgentCard:
    """Fetch an Agent Card over HTTP. Uses httpx if available, else urllib."""
    try:
        import httpx  # type: ignore

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except ImportError:
        import urllib.request

        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # network / parse
        raise AgentCardError(f"Could not fetch card at {url!r}: {e}") from e
    return parse_card(data)


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def load_card(source: Any) -> AgentCard:
    """Load an Agent Card from any supported source.

    Accepts:
      - an AgentCard (returned as-is)
      - a dict shaped like an Agent Card
      - a URL string ending in /.well-known/agent-card.json or any card URL
      - a local filesystem path
    """
    if isinstance(source, AgentCard):
        return source
    if isinstance(source, dict):
        return parse_card(source)
    if isinstance(source, str):
        if is_url(source):
            return load_card_from_url(source)
        return load_card_from_path(source)
    raise AgentCardError(
        f"Unsupported card source type: {type(source).__name__}. "
        "Pass an AgentCard, dict, URL, or file path."
    )
