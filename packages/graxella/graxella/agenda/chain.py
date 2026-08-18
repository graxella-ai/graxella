"""graxella.agenda.chain — chain-healing miner (Phase 2, task 2-7).

Reads the trajectory-escalation signals the runtime records (loops,
exhausted budgets, failed hops) and, where the same (domain, agent,
status) pattern repeats, emits a spec.Proposal(kind=playbook) — an
ACE-style append-only delta of concrete guidance for that agent in that
domain, cited per escalation, gate-decided like everything else.
"""
from __future__ import annotations

from collections import defaultdict

from graxella.beliefs.adapter import Memory
from graxella.gate.spec import (ArtifactKind, EvidenceCitation, EvidenceRole,
                                Proposal, TargetScope)

_ADVICE = {
    "loop_detected": ("You have previously handed identical work back and "
                      "forth. Before emitting HANDOFF, check whether the "
                      "task already contains your own prior output; if so, "
                      "finish it yourself or escalate to a human."),
    "budget_exhausted": ("Your chains have exhausted their budgets. Prefer "
                         "completing in fewer hops: batch related subtasks "
                         "into one HANDOFF instead of chaining them."),
    "failed": ("A hop you initiated failed outright. Verify the target "
               "peer and task wording before handing off."),
}


class ChainMiner:
    """Escalation signals → playbook proposals, when patterns repeat."""

    name = "chain_miner"
    min_support = 2

    def __init__(self, memory: Memory) -> None:
        self.memory = memory

    def mine(self) -> list[Proposal]:
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for sig in self.memory.signals(kind="trajectory_escalation"):
            key = (str(sig.get("domain") or "default"),
                   str(sig.get("agent") or "?"),
                   str(sig.get("status") or "failed"))
            groups[key].append(sig)

        out: list[Proposal] = []
        for (domain, agent, status), sigs in sorted(groups.items()):
            if len(sigs) < self.min_support:
                continue
            target = TargetScope(domain=domain, agent=agent)
            payload = {
                "issue": f"chain_{status}",
                "occurrences": len(sigs),
                "delta_items": [_ADVICE.get(status, _ADVICE["failed"])],
                "trajectories": [s.get("trajectory_id") for s in sigs[:5]],
            }
            out.append(Proposal(
                id=Proposal.deterministic_id(
                    ArtifactKind.PLAYBOOK, target,
                    {"issue": f"chain_{status}"}),
                kind=ArtifactKind.PLAYBOOK,
                target=target,
                payload=payload,
                origin=f"miner:{self.name}",
                evidence=tuple(
                    EvidenceCitation(assertion_id=s["assertion_id"],
                                     role=EvidenceRole.EPISODE,
                                     note=f"escalation: {status}")
                    for s in sigs),
                confidence=min(0.3 + 0.1 * len(sigs), 0.9),
            ))
        return out


__all__ = ["ChainMiner"]
