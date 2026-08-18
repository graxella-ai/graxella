"""graxella.agenda.mismatch — reasoning–action mismatch miner (task 1-7).

MAST FM-2.6: an agent SAYS it did something its tool trail doesn't show.
The live detector in ``InstrumentedApp.route()`` records each occurrence
as a ledger signal (predicate="signal", object="reasoning_action_mismatch",
derived_from=the decision). This miner reads those signals back and,
where the pattern repeats for the same (domain, agent), emits a
spec.Proposal(kind=prompt) — the fix for a chronic claimer is a prompt
correction, shipped through the gate like every other behavior change.

Deterministic: same ledger, same proposals. First miner born speaking
the canonical Proposal — no legacy shape, no bridge.
"""
from __future__ import annotations

from collections import defaultdict

from graxella.beliefs.adapter import Memory
from graxella.gate.spec import (
    ArtifactKind,
    EvidenceCitation,
    EvidenceRole,
    Proposal,
    TargetScope,
)

SIGNAL_KIND = "reasoning_action_mismatch"


class MismatchMiner:
    """Signals → proposals, when the pattern repeats."""

    name = "mismatch_miner"
    min_support = 2   # occurrences per (domain, agent) before proposing

    def __init__(self, memory: Memory) -> None:
        self.memory = memory

    def mine(self) -> list[Proposal]:
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for sig in self.memory.signals(kind=SIGNAL_KIND):
            domain = str(sig.get("domain") or "default")
            agent = str(sig.get("agent") or "?")
            groups[(domain, agent)].append(sig)

        proposals: list[Proposal] = []
        for (domain, agent), sigs in sorted(groups.items()):
            if len(sigs) < self.min_support:
                continue
            claims = sorted({s.get("claim", "?") for s in sigs})
            target = TargetScope(domain=domain, agent=agent)
            payload = {
                "issue": SIGNAL_KIND,
                "occurrences": len(sigs),
                "claims": claims,
                "instruction_delta": (
                    "Only state that an action was performed when a tool "
                    "call actually performed it; otherwise say what you "
                    "WOULD do and which tool it requires."
                ),
                "examples": [s.get("response_head", "")[:120] for s in sigs[:3]],
            }
            proposals.append(Proposal(
                id=Proposal.deterministic_id(ArtifactKind.PROMPT, target,
                                             {"issue": SIGNAL_KIND,
                                              "claims": claims}),
                kind=ArtifactKind.PROMPT,
                target=target,
                payload=payload,
                origin=f"miner:{self.name}",
                evidence=tuple(
                    EvidenceCitation(assertion_id=s["assertion_id"],
                                     role=EvidenceRole.EPISODE,
                                     note=f"claimed '{s.get('claim')}' "
                                          f"with no tool call")
                    for s in sigs
                ),
                confidence=min(0.3 + 0.1 * len(sigs), 0.9),
            ))
        return proposals


__all__ = ["MismatchMiner", "SIGNAL_KIND"]
