"""graxella.gate.audit — paired-replay evidence (Phase 1, task 1-6).

The SkillAudit pattern, adapted: validate a candidate artifact by
replaying it against matched historical cases and diffing the results —
no ground-truth labels, no LLM. The win/loss table is recorded in the
ledger (predicate="paired_replay", derived_from=the case sources) and
attached to the proposal as an EvidenceCitation(role=paired_replay), so
a proposal arrives at review carrying its own audit.

The Evidence Gate fuses replay counts into the posterior — but replay
evidence alone can never auto-approve: the provenance-diversity floor
counts OPERATIONAL sessions, and a replay table is a single source. It
tips borderline warm tuples and informs humans on cold ones; it does
not replace lived outcomes.

Case sourcing: callers supply ReplayCases. The natural producer is the
Phase 2 healer (task 2-5), which holds the failed and succeeded raw args
at heal time. (Episodes store only arg hashes — by design — so replay
cases cannot be reconstructed from the episode store after the fact.)
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from graxella.beliefs.adapter import Memory
from graxella.gate.spec import EvidenceCitation, EvidenceRole, Proposal
from graxella.healing.recipes import TransformRecipe


class ReplayCase(BaseModel):
    """One historical case: inputs that failed, and the output shape a
    matched successful episode proves is correct."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1)
    inputs: dict[str, Any]
    expected: dict[str, Any]
    source_ids: tuple[str, ...] = ()   # episode / assertion citations


class CaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    win: bool
    got: Optional[dict[str, Any]] = None
    note: str = ""


class ReplayReport(BaseModel):
    """The win/loss table for one proposal."""

    model_config = ConfigDict(frozen=True)

    proposal_id: str
    wins: int
    losses: int
    results: tuple[CaseResult, ...]
    assertion_id: Optional[str] = None   # set when recorded in the ledger

    @property
    def total(self) -> int:
        return self.wins + self.losses


#: apply_fn(proposal, inputs) -> outputs. Deterministic, side-effect
#: free — replay never touches live tools.
ApplyFn = Callable[[Proposal, dict[str, Any]], dict[str, Any]]


def apply_transform(proposal: Proposal, inputs: dict[str, Any]) -> dict[str, Any]:
    """The built-in ApplyFn for transform-shaped payloads: rebuild the
    TransformRecipe from the proposal payload and apply it. Handles both
    kind=transform payloads (recipe fields at top level) and
    kind=tool_binding payloads (recipe nested under "recipe")."""
    src = proposal.payload
    recipe_dict = src.get("recipe") if isinstance(src.get("recipe"), dict) else src
    recipe = TransformRecipe(
        field_map=dict(recipe_dict.get("field_map") or {}),
        static_defaults=dict(recipe_dict.get("static_defaults") or {}),
        drop_fields=tuple(recipe_dict.get("drop_fields") or ()),
    )
    return recipe.apply(inputs)


def audit(proposal: Proposal, cases: list[ReplayCase], *,
          apply_fn: ApplyFn = apply_transform,
          memory: Memory | None = None) -> ReplayReport:
    """Replay ``proposal`` against ``cases`` and diff. A case is a WIN
    when the candidate reproduces the historically-successful output
    exactly. Exceptions are losses, never crashes — a broken candidate
    is evidence too."""
    results: list[CaseResult] = []
    for case in cases:
        try:
            got = apply_fn(proposal, dict(case.inputs))
            win = got == case.expected
            note = "" if win else "output differs from matched success"
            results.append(CaseResult(case_id=case.case_id, win=win,
                                      got=got, note=note))
        except Exception as exc:
            results.append(CaseResult(case_id=case.case_id, win=False,
                                      note=f"{type(exc).__name__}: {exc}"))
    wins = sum(1 for r in results if r.win)
    report = ReplayReport(proposal_id=proposal.id, wins=wins,
                          losses=len(results) - wins, results=tuple(results))

    if memory is not None:
        source_ids = tuple(sid for c in cases for sid in c.source_ids)
        aid = memory._client.observe(
            report.model_dump_json(),
            subject=proposal.id,
            predicate="paired_replay",
            object=f"{wins}/{report.total}",
            confidence=1.0,
            source_id="paired-replay-auditor",
            derived_from=source_ids,
        )
        report = report.model_copy(update={"assertion_id": aid})
    return report


def with_replay_evidence(proposal: Proposal, report: ReplayReport) -> Proposal:
    """Attach the recorded replay report to the proposal as a citation.
    Requires the report to have been recorded (assertion_id set)."""
    if report.assertion_id is None:
        raise ValueError("record the report first: audit(..., memory=...)")
    data = proposal.model_dump()
    data["evidence"] = tuple(proposal.evidence) + (EvidenceCitation(
        assertion_id=report.assertion_id,
        role=EvidenceRole.PAIRED_REPLAY,
        note=f"replay {report.wins}/{report.total} wins",
    ),)
    return Proposal.model_validate(data)


def replay_counts_for(memory: Memory, proposal_id: str) -> tuple[int, int]:
    """(wins, losses) across all recorded replay reports for a proposal.
    Used by the Evidence Gate's posterior fusion."""
    wins = losses = 0
    for row in memory.beliefs(subject=proposal_id, predicate="paired_replay"):
        data = json.loads(row["statement"])
        wins += int(data.get("wins") or 0)
        losses += int(data.get("losses") or 0)
    return wins, losses


__all__ = [
    "ReplayCase", "CaseResult", "ReplayReport",
    "audit", "apply_transform", "with_replay_evidence", "replay_counts_for",
]
