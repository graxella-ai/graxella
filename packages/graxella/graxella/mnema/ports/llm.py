"""LLM port — boundary between the consolidator and any language model backend.

Imports pydantic + stdlib only (I5). Adapters live in adapters/llm/.
"""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ContradictionProposal lives in core.graph; re-exported here so LLM adapters
# only need to import from ports.llm (keeps the boundary clean).
from graxella.mnema.core.graph import ContradictionProposal as ContradictionProposal  # noqa: F401

_SNAKE = re.compile(r"^[a-z_][a-z0-9_]*$")


class LLMCallError(RuntimeError):
    """Raised by LLM adapters when the backend call fails or returns unparseable output.

    Callers (e.g. SleepConsolidator) must catch this and abort — never silently
    return empty proposals, which would overwrite prior rules with nothing.
    """


class RuleProposal(BaseModel):
    """A proposed Rule returned by the LLM — not yet stored."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    derived_from: tuple[str, ...] = Field(min_length=1)

    @field_validator("derived_from")
    @classmethod
    def _nonempty_ids(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if any(not s for s in v):
            raise ValueError("derived_from ids must be non-empty strings")
        return v


class SkillProposal(BaseModel):
    """A proposed Skill returned by the LLM — not yet stored or ast-validated."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    code: str = Field(min_length=1)
    signature: str = Field(min_length=1)
    derived_from: tuple[str, ...] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _snake_case(cls, v: str) -> str:
        if not _SNAKE.match(v):
            raise ValueError(f"skill name must be snake_case, got {v!r}")
        return v

    @field_validator("derived_from")
    @classmethod
    def _nonempty_ids(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if any(not s for s in v):
            raise ValueError("derived_from ids must be non-empty strings")
        return v


class LLMPort(Protocol):
    """Contract that every LLM adapter must satisfy."""

    def extract_rules(
        self,
        assertions: list[dict],
        *,
        max_rules: int = 10,
    ) -> list[RuleProposal]:
        """Return up to max_rules rules distilled from the assertion dicts."""
        ...

    def extract_skills(
        self,
        assertions: list[dict],
        *,
        max_skills: int = 5,
    ) -> list[SkillProposal]:
        """Return up to max_skills skill proposals distilled from assertions."""
        ...

    def detect_contradictions(
        self,
        assertions: list[dict],
    ) -> list[ContradictionProposal]:
        """Return pairs of assertion IDs that logically contradict each other.

        Each pair comes with a reason string explaining the conflict.
        Adapters should be conservative — only flag clear factual conflicts,
        not differences of emphasis or scope.
        """
        ...

    def answer_question(self, context: str, question: str) -> str:
        """Answer a question given a retrieved context string.

        Used by benchmarks (LongMemEval). Returns a short direct answer.
        Should return "unknown" if the context does not contain the answer.
        """
        ...
