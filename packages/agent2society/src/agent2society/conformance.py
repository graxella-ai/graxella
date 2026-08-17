"""Conformance guardrail.

Before any handoff, agent2society answers two questions about the chosen
(agent, skill) pair:

  1. Does the agent's card actually declare this skill?
  2. Is the task inside the agent's declared boundary (allow/deny lists)?

If either check fails, the dispatch is blocked -- the library does NOT let
an agent silently accept work it never claimed it could do. This is
detection and prevention, not self-correction.

Both task and boundary terms are NFKC-normalised before comparison so an
attacker cannot dodge a deny-list term by substituting visually-identical
Unicode characters (e.g. fullwidth Latin or compatibility forms).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Optional

from .graph import AgentNode, CapabilityGraph


@dataclass(frozen=True)
class ConformanceResult:
    ok: bool
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason}


def _normalise(s: str) -> str:
    """Casefold + NFKC so visually identical Unicode forms compare equal.

    NFKC collapses compatibility variants (fullwidth ASCII, ligatures,
    superscripts) into their canonical forms before casefolding. This
    closes the substring-bypass vector where a deny term "refund" could
    miss a task containing the fullwidth "ｒｅｆｕｎｄ".
    """
    return unicodedata.normalize("NFKC", s or "").casefold()


def check(
    graph: CapabilityGraph,
    *,
    agent: str,
    skill_id: str,
    task: str,
) -> ConformanceResult:
    node = graph.get(agent)
    if node is None:
        return ConformanceResult(False, f"agent {agent!r} not in mesh")

    if not node.card.declares(skill_id):
        return ConformanceResult(
            False,
            f"agent {agent!r} does not declare skill {skill_id!r}",
        )

    boundary = node.boundary
    haystack = _normalise(task)
    for term in boundary.deny:
        if term and _normalise(term) in haystack:
            return ConformanceResult(
                False,
                f"task touches denied term {term!r}",
            )

    if boundary.allow:
        if not any(term and _normalise(term) in haystack for term in boundary.allow):
            return ConformanceResult(
                False,
                f"task does not match any allow term in {boundary.allow!r}",
            )

    return ConformanceResult(True)
