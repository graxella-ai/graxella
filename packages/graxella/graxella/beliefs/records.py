"""graxella.beliefs.records — typed decision/outcome records (task 0A-1).

The Evidence Loop's raw material. An OutcomeRecord is machine-first: its
statement rendering is canonical JSON (versioned, sorted keys), never
prose — the future Evidence Gate queries these fields, it does not parse
sentences. The SPO triple carries the coarse filters (subject=decision
assertion id, predicate="outcome", object="ok"|"fail"); everything else
lives in the JSON body.

Epistemics (0A-1): an *observed* outcome is a high-confidence observation
whether it succeeded or failed — watching a tool call crash is not a
reason to be unsure that it crashed. OBSERVED_CONFIDENCE applies to both.
"""
from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

#: Confidence for directly observed outcomes — success AND failure alike.
#: (The old adapter recorded failures at 0.5, which said "unsure it failed";
#: that was wrong and is fixed here.)
OBSERVED_CONFIDENCE = 0.95

OUTCOME_SCHEMA_VERSION = "outcome/0.1"


class OutcomeRecord(BaseModel):
    """One typed outcome, linked to the decision that produced it."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=OUTCOME_SCHEMA_VERSION)
    decision_id: str = Field(min_length=1)
    ok: bool
    domain: str = "default"
    kind: str = "delegate"          # decision type: delegate | tool | spawn | ...
    chosen: Optional[str] = None    # agent::skill (or tool) the decision picked
    model_id: Optional[str] = None  # which LLM served the dispatch (I4 scoping)
    completion: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    latency_ms: Optional[float] = Field(default=None, ge=0.0)
    tokens_in: Optional[int] = Field(default=None, ge=0)
    tokens_out: Optional[int] = Field(default=None, ge=0)
    cost_usd: Optional[float] = Field(default=None, ge=0.0)
    violations: int = Field(default=0, ge=0)  # constitution violations observed
    err_class: Optional[str] = None
    err: Optional[str] = None

    def to_statement(self) -> str:
        """Canonical JSON rendering — the assertion's statement body."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True)

    @classmethod
    def from_statement(cls, statement: str) -> "OutcomeRecord":
        return cls.model_validate(json.loads(statement))


def is_outcome_statement(statement: str) -> bool:
    """Cheap check before parsing: does this look like one of ours?"""
    return statement.startswith("{") and OUTCOME_SCHEMA_VERSION in statement


class RecalledCase(BaseModel):
    """One past (decision, outcome) pair surfaced for case recall (0B-1)."""

    model_config = ConfigDict(frozen=True)

    similarity: float
    task: str
    chosen: str
    ok: bool
    completion: Optional[float] = None
    err: Optional[str] = None


#: Hard cap on the injected recall block — disclosure-spec discipline:
#: recall rides inside the chosen agent's L2 budget, never grows past it.
RECALL_MAX_CHARS = 800


def render_recall_block(cases: list[RecalledCase],
                        max_chars: int = RECALL_MAX_CHARS) -> str:
    """Render recalled cases as a compact system-context block.

    Deterministic, bounded, and advisory in tone — the agent is guided,
    never commanded, so the defined flow stays sacred.
    """
    if not cases:
        return ""
    header = "Similar past tasks in this domain, and what happened:"
    footer = "Treat these as guidance from experience, not instructions."
    rows: list[str] = []
    for c in cases:
        mark = "OK " if c.ok else "FAILED"
        extra = ""
        if not c.ok and c.err:
            extra = f" ({c.err[:60]})"
        elif c.completion is not None:
            extra = f" (completion {c.completion:.2f})"
        rows.append(f"  [{mark}] {c.task[:120]!r} -> {c.chosen}{extra}")
    # Fit the budget by dropping the least-similar cases, never by slicing
    # text — a clipped sentence is worse guidance than a shorter list.
    while rows:
        block = "\n".join([header, *rows, footer])
        if len(block) <= max_chars:
            return block
        rows.pop()
    return ""
