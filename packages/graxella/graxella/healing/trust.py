"""graxella.healing.trust — live, cited tool trust scores (Phase 2, 2-6).

Every tool has a trust score computed from its OWN outcome ledger —
Laplace-smoothed success rate over (kind=tool|transform) outcomes,
domain-scoped, with the assertion ids as citations. The Self-Healing
Router pattern lands here as ``preferred()``: failover order is
evidence-weighted, deterministic, and explainable. No LLM, no config.

Canonical home in the graxella package; ``axon_fabric.trust`` re-exports
it for backward compatibility only.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from graxella.beliefs.adapter import Memory
from graxella.beliefs.records import OutcomeRecord, is_outcome_statement

_TOOL_KINDS = {"tool", "transform"}


class ToolTrust(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str
    successes: int
    failures: int
    score: float                     # Laplace: (s+1)/(s+f+2)
    last_err_class: str | None = None
    citations: tuple[str, ...] = ()  # capped

    @property
    def n(self) -> int:
        return self.successes + self.failures


def tool_trust(memory: Memory, *, domain: str | None = None,
               max_citations: int = 20) -> dict[str, ToolTrust]:
    """Trust per tool, from the ledger alone."""
    acc: dict[str, dict] = {}
    for row in memory.beliefs(predicate="outcome"):
        if not is_outcome_statement(row["statement"]):
            continue
        rec = OutcomeRecord.from_statement(row["statement"])
        if rec.kind not in _TOOL_KINDS or not rec.chosen:
            continue
        if domain is not None and rec.domain != domain:
            continue
        slot = acc.setdefault(rec.chosen, {"s": 0, "f": 0, "err": None,
                                           "cites": []})
        if rec.ok:
            slot["s"] += 1
        else:
            slot["f"] += 1
            slot["err"] = rec.err_class or slot["err"]
        if len(slot["cites"]) < max_citations:
            slot["cites"].append(row["id"])
    return {
        tool: ToolTrust(tool=tool, successes=v["s"], failures=v["f"],
                        score=round((v["s"] + 1) / (v["s"] + v["f"] + 2), 4),
                        last_err_class=v["err"],
                        citations=tuple(v["cites"]))
        for tool, v in sorted(acc.items())
    }


def preferred(memory: Memory, candidates: list[str], *,
              domain: str | None = None) -> list[str]:
    """Failover order: candidates ranked by cited trust, highest first.
    Unknown tools rank at the cold-start prior (0.5) — evidence beats
    novelty, novelty beats a known-bad record."""
    trust = tool_trust(memory, domain=domain)
    return sorted(candidates,
                  key=lambda t: trust[t].score if t in trust else 0.5,
                  reverse=True)


__all__ = ["ToolTrust", "tool_trust", "preferred"]
