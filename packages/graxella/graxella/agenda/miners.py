"""Deterministic miners over the Experience store.

Three first-cut miners:

  * RuleDistiller       — turns repeated (failed → succeeded) tool pairs into
                          a proposed skill-substitution Rule.
  * CapabilityReweigher — rolling success rate per (intent, agent) → propose
                          a routing weight adjustment.
  * TrustPromoter       — count clean canary runs per agent → propose a
                          trust tier promotion (NEW → PROBATIONARY → STABLE).

Each miner returns a list of Proposal objects with `derived_from` citing the
exact episode ids they used, so any downstream action is auditable back to
lived experience. Nothing here is stochastic; the same store yields the
same proposals — a property the reviewer relies on.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from graxella.memory.episode import ExperienceEpisode, ExperienceStore


def _pid() -> str:
    """Fallback id — real proposals use `_proposal_id(kind, subject)` so the
    same evidence always yields the same id (idempotent audits, stable
    promote-by-id after re-mine)."""
    import uuid
    return f"prop_{uuid.uuid4().hex[:10]}"


def _proposal_id(kind: str, subject: str) -> str:
    """Deterministic id from the semantic identity of a proposal.

    Re-mining the same store must yield the same ids so `promote --id`
    remains valid after fresh mines, and the audit graph's cross-references
    (rule.wasDerivedFrom -> proposal) don't dangle.
    """
    h = hashlib.sha256(f"{kind}|{subject}".encode()).hexdigest()
    return f"prop_{h[:10]}"


@dataclass
class Proposal:
    """One graph-mutation suggestion. Never applied automatically.

    .. deprecated:: Promotion Spec v0.1
        Superseded by ``graxella.gate.spec.Proposal`` — the single typed
        lifecycle for all learnable artifacts (docs/specs/promotion-spec.md).
        Miners re-emit spec Proposals in Phase 1 (build plan task 1-5);
        field mapping: kind→ArtifactKind, subject→target, change→payload,
        derived_from→evidence[role=episode], evidence(str)→note.

    kind          - what the proposal touches: "rule" | "route_weight" | "trust_tier"
    subject       - the thing the proposal targets (skill name, agent uri, etc.)
    change        - opaque dict describing the mutation
    evidence      - short human-readable rationale
    derived_from  - episode ids that produced the signal
    confidence    - 0..1 monotone with strength of evidence
    """
    id: str = field(default_factory=_pid)
    kind: str = ""
    subject: str = ""
    change: dict[str, Any] = field(default_factory=dict)
    evidence: str = ""
    derived_from: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Miner:
    """Base miner contract. Implementations are pure functions of the store."""

    name: str = "miner"

    def mine(self, episodes: list[ExperienceEpisode]) -> list[Proposal]:
        raise NotImplementedError


class RuleDistiller(Miner):
    """(fail → success) pairs on the same intent become a substitution rule.

    Signal: within one session, tool A fails then tool B succeeds for the same
    intent. Two independent sessions showing the same substitution promotes it
    from noise to a rule candidate.
    """
    name = "rule_distiller"
    min_support = 2  # distinct sessions

    def mine(self, episodes: list[ExperienceEpisode]) -> list[Proposal]:
        by_session: dict[str, list[ExperienceEpisode]] = defaultdict(list)
        for e in episodes:
            by_session[e.session_id].append(e)

        # (intent, failed_skill, succeeded_skill) -> [episode_ids...]
        pairs: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        session_hits: dict[tuple[str, str, str], set[str]] = defaultdict(set)

        # Shape A — AXON: 1 dispatch = 1 skill = 1 Episode.
        # A failed Episode followed later in the session by a succeeded
        # Episode on the same intent + different skill is a candidate.
        for sess, eps in by_session.items():
            eps.sort(key=lambda e: e.started_at)
            for i, cur in enumerate(eps):
                if cur.ok or not cur.skill:
                    continue
                for nxt in eps[i + 1:]:
                    if (nxt.intent == cur.intent and nxt.ok
                            and nxt.skill and nxt.skill != cur.skill):
                        k = (cur.intent, cur.skill, nxt.skill)
                        pairs[k].extend([cur.id, nxt.id])
                        session_hits[k].add(sess)
                        break

        # Shape B — LangGraph/CrewAI: many tool_calls inside one Episode.
        # A failed tool immediately followed by a succeeded tool inside the
        # same Episode is the same signal — attribute both to the Episode
        # and count the Episode's session as one support unit.
        for e in episodes:
            if not e.tool_calls:
                continue
            for i, tc in enumerate(e.tool_calls):
                if tc.ok:
                    continue
                for nxt in e.tool_calls[i + 1:]:
                    if nxt.ok and nxt.tool_id and nxt.tool_id != tc.tool_id:
                        k = (e.intent, tc.tool_id, nxt.tool_id)
                        pairs[k].append(e.id)
                        session_hits[k].add(e.session_id)
                        break

        proposals: list[Proposal] = []
        for (intent, bad, good), episode_ids in pairs.items():
            support = len(session_hits[(intent, bad, good)])
            if support < self.min_support:
                continue
            subject = f"{intent}:{bad}->{good}"
            proposals.append(Proposal(
                id=_proposal_id("rule", subject),
                kind="rule",
                subject=subject,
                change={"if_intent": intent, "replace_skill": bad,
                        "with_skill": good},
                evidence=(f"{bad} failed then {good} succeeded across "
                          f"{support} independent sessions"),
                derived_from=sorted(set(episode_ids)),
                confidence=min(1.0, support / 4.0),
            ))
        return proposals


class CapabilityReweigher(Miner):
    """Rolling success rate per (intent, agent) → routing weight suggestion.

    Only proposes a change when the observed rate diverges from the neutral
    weight by a wide margin — otherwise the noise buries the signal.
    """
    name = "capability_reweigher"
    min_samples = 3
    delta_threshold = 0.25  # only propose if |rate - 0.5| exceeds this

    def mine(self, episodes: list[ExperienceEpisode]) -> list[Proposal]:
        buckets: dict[tuple[str, str], list[ExperienceEpisode]] = defaultdict(list)
        for e in episodes:
            if not e.agent_uri or not e.intent:
                continue
            buckets[(e.intent, e.agent_uri)].append(e)

        proposals: list[Proposal] = []
        for (intent, agent), eps in buckets.items():
            n = len(eps)
            if n < self.min_samples:
                continue
            rate = sum(1 for e in eps if e.ok) / n
            if abs(rate - 0.5) < self.delta_threshold:
                continue
            direction = "boost" if rate > 0.5 else "penalize"
            subject = f"{intent}@{agent}"
            proposals.append(Proposal(
                id=_proposal_id("route_weight", subject),
                kind="route_weight",
                subject=subject,
                change={"intent": intent, "agent_uri": agent,
                        "success_rate": round(rate, 3),
                        "direction": direction},
                evidence=(f"{agent} succeeded {int(rate*n)}/{n} on intent "
                          f"'{intent}' — {direction} suggested"),
                derived_from=[e.id for e in eps],
                confidence=min(1.0, n / 10.0),
            ))
        return proposals


class TrustPromoter(Miner):
    """Clean canary runs on read-only intents → propose a trust tier bump.

    Conservative on purpose: only read-only side-effects count as canaries;
    a single failure resets the counter for that agent.
    """
    name = "trust_promoter"
    promote_at = 3  # clean canary runs to earn a promotion proposal

    def mine(self, episodes: list[ExperienceEpisode]) -> list[Proposal]:
        by_agent: dict[str, list[ExperienceEpisode]] = defaultdict(list)
        for e in episodes:
            if e.agent_uri:
                by_agent[e.agent_uri].append(e)

        proposals: list[Proposal] = []
        for agent, eps in by_agent.items():
            eps.sort(key=lambda e: e.started_at)
            streak: list[ExperienceEpisode] = []
            for e in eps:
                if e.side_effect_class != "read_only":
                    streak = []
                    continue
                if not e.ok:
                    streak = []
                    continue
                streak.append(e)
            if len(streak) < self.promote_at:
                continue
            proposals.append(Proposal(
                id=_proposal_id("trust_tier", agent),
                kind="trust_tier",
                subject=agent,
                change={"agent_uri": agent, "from": "NEW", "to": "PROBATIONARY",
                        "clean_runs": len(streak)},
                evidence=(f"{len(streak)} consecutive clean read-only runs "
                          f"— safe to advance one tier"),
                derived_from=[e.id for e in streak],
                confidence=min(1.0, len(streak) / 6.0),
            ))
        return proposals


DEFAULT_MINERS: list[Miner] = [
    RuleDistiller(),
    CapabilityReweigher(),
    TrustPromoter(),
]
