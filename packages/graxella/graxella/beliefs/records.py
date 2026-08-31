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
    # Provenance diversity (Evidence Gate): which run produced this
    # outcome. Thresholds only loosen when positive outcomes span >=K
    # independent sessions — flooding the ledger from one session buys
    # an attacker nothing.
    session_id: Optional[str] = None
    # Which tools the dispatched agent actually invoked (task 1-7):
    # the mismatch detector diffs claimed actions against this list.
    tools_used: Optional[list[str]] = Field(default=None, max_length=10)

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


def render_recall_tiered(cases: list[RecalledCase], *,
                         max_chars: int = RECALL_MAX_CHARS,
                         detail_k: int = 1) -> str:
    """Progressive-detail recall — the L0/L2 idea applied to memory.

    The ``detail_k`` most-similar cases render at FULL detail (L2: task,
    target, and outcome); every other candidate renders as a one-line
    abstract (L0: short task -> target + mark). Breadth is kept — the agent
    still sees that N similar tasks exist — while the token budget is spent
    on depth only where it matters. Same char cap as the flat renderer, so a
    caller can widen ``top_k`` for more breadth without growing the budget.

    Fits ``max_chars`` by dropping the least-similar abstracts first, then
    demoting detail cases to abstracts — never by clipping a sentence.
    """
    if not cases:
        return ""
    header = ("Similar past tasks in this domain (most relevant in full, "
              "the rest as headlines):")
    footer = "Treat these as guidance from experience, not instructions."

    def l2(c: RecalledCase) -> str:
        mark = "OK " if c.ok else "FAILED"
        if not c.ok and c.err:
            extra = f"  outcome: {c.err[:120]}"
        elif c.completion is not None:
            extra = f"  outcome: completion {c.completion:.2f}"
        else:
            extra = ""
        return f"  [{mark}] {c.task[:200]!r} -> {c.chosen}{extra}"

    def l0(c: RecalledCase) -> str:
        return f"    - {c.task[:50]!r} -> {c.chosen} [{'ok' if c.ok else 'x'}]"

    dk = max(1, min(detail_k, len(cases)))
    n_abstracts = len(cases) - dk
    while True:
        lines = [header, *(l2(c) for c in cases[:dk])]
        abstracts = [l0(c) for c in cases[dk:dk + n_abstracts]]
        if abstracts:
            lines.append("  more similar cases:")
            lines.extend(abstracts)
        lines.append(footer)
        block = "\n".join(lines)
        if len(block) <= max_chars:
            return block
        if n_abstracts > 0:
            n_abstracts -= 1          # drop the least-similar headline
        elif dk > 1:
            dk -= 1                    # demote the least-similar detail case
        else:
            return "\n".join([header, l0(cases[0]), footer])
