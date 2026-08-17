"""graxella.gate.evidence — the Evidence Gate (Phase 1, tasks 1-1…1-4).

The memory-grounded replacement for the deprecated scored ``GatePolicy``.
No scoring rubric, no rule DSL, no LLM judge. One question, answered from
the ledger: what happened the last N times this kind of change was made
in this exact (domain, kind, target, model) tuple?

Mechanics — deterministic end to end:

  * Prior (1-1): typed outcomes for the gate tuple → Beta-Bernoulli
    posterior. Evidence contract: an outcome counts toward a tuple when
    its ``kind`` equals the artifact kind, its ``domain``/``model_id``
    match, and its ``chosen`` covers the target scope. When Phase 2
    healing applies a promoted transform, it records outcomes under that
    tuple — which is exactly how the next proposal of the same shape
    finds a warm prior.
  * Decision (1-2): constitution/hard blocks first (never evidence-
    overridable) → cold start = NEEDS_HUMAN → posterior vs threshold,
    guarded by provenance diversity (successes must span ≥K independent
    sessions) and blast radius (wide requires overwhelming same-tuple
    evidence).
  * Threshold (1-3): self-calibrating per tuple, closed form —
        thr(n) = THR_FLOOR + THR_SPAN * exp(-n_success / THR_HALF)
    Strict at n=0 (0.95), relaxing toward 0.85 as confirmed successes
    accrue for THAT tuple only. No hand-tuned weights anywhere.
  * Verdict (1-4): every decision is written back to the ledger as a
    cited assertion (subject=proposal id, predicate="gate_verdict",
    derived_from=the evidence ids). ``why()`` is a lookup, not
    forensics.

Zero LLM in any code path of this module.
"""
from __future__ import annotations

import json
import math
from enum import Enum
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from graxella.beliefs.adapter import Memory
from graxella.beliefs.records import OutcomeRecord, is_outcome_statement
from graxella.gate.spec import (
    ArtifactKind,
    BlastRadius,
    EvidenceCitation,
    EvidenceRole,
    Proposal,
    ProposalStatus,
    TargetScope,
)

# -- calibration constants (documented curve, not tunable opinions) ---------
THR_FLOOR = 0.85     # the threshold never relaxes below this
THR_SPAN = 0.10      # cold-start threshold = FLOOR + SPAN = 0.95
THR_HALF = 20.0      # successes for ~63% of the relaxation
REJECT_BELOW = 0.50  # posterior below this (with enough data) auto-rejects
MIN_N_FOR_REJECT = 5  # never auto-reject on thin evidence — ask a human
WIDE_MIN_SUCCESSES = 25  # wide blast needs overwhelming same-tuple evidence
MAX_CITATIONS = 50


def threshold_for(n_successes: int) -> float:
    """The self-calibrating per-tuple threshold (task 1-3)."""
    return THR_FLOOR + THR_SPAN * math.exp(-n_successes / THR_HALF)


class GateDecision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    NEEDS_HUMAN = "needs_human"
    AUTO_REJECT = "auto_reject"


class EvidencePrior(BaseModel):
    """The ledger's answer for one gate tuple (task 1-1)."""

    model_config = ConfigDict(frozen=True)

    successes: int = 0
    failures: int = 0
    sessions: int = 0            # distinct session_ids among successes
    citations: tuple[str, ...] = ()  # assertion ids, capped at MAX_CITATIONS

    @property
    def n(self) -> int:
        return self.successes + self.failures

    @property
    def alpha(self) -> float:
        return self.successes + 1.0

    @property
    def beta(self) -> float:
        return self.failures + 1.0

    @property
    def posterior_mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


class GateVerdict(BaseModel):
    """One gate decision, fully explainable (task 1-2/1-4)."""

    model_config = ConfigDict(frozen=True)

    proposal_id: str
    decision: GateDecision
    posterior: float
    threshold: float
    prior: EvidencePrior
    domain: str
    kind: str
    blast_radius: str
    diversity_required: int
    reason: str
    guards: tuple[str, ...] = ()
    verdict_assertion_id: Optional[str] = None

    def render(self) -> str:
        """The charter's ``gate.why()`` output, for humans."""
        p = self.prior
        cites = ", ".join(p.citations[:3])
        more = f", +{len(p.citations) - 3}" if len(p.citations) > 3 else ""
        return "\n".join([
            f"decision:   {self.decision.value.upper()}",
            f"posterior:  {self.posterior:.2f}  "
            f"(Beta({p.alpha:.0f}, {p.beta:.0f}) over domain={self.domain}, "
            f"kind={self.kind})",
            f"evidence:   {p.successes} successes · {p.failures} failures · "
            f"{p.sessions} sessions",
            f"citations:  [{cites}{more}]" if p.citations else "citations:  []",
            f"threshold:  {self.threshold:.2f}  "
            f"(self-calibrated; cold-start=NEEDS_HUMAN)",
            f"guards:     {' · '.join(self.guards) if self.guards else '—'}",
            f"reason:     {self.reason}",
        ])


#: A hard block inspects a Proposal and returns a rejection reason or
#: None. Constitution invariants plug in here — and are checked BEFORE
#: any evidence is even looked at (I: evidence never overrides them).
HardBlock = Callable[[Proposal], Optional[str]]


class EvidenceGate:
    """The gate. Construct once with the Memory whose ledger holds the
    evidence; call ``decide(proposal)`` to evaluate + transition + record.
    """

    def __init__(self, memory: Memory, *,
                 k_diversity: int = 3,
                 wide_min_successes: int = WIDE_MIN_SUCCESSES,
                 hard_blocks: tuple[HardBlock, ...] = (),
                 constitution=None) -> None:
        self.memory = memory
        self.k_diversity = k_diversity
        self.wide_min_successes = wide_min_successes
        self.hard_blocks: tuple[HardBlock, ...] = hard_blocks
        if constitution is not None:
            self.hard_blocks += (_constitution_block(constitution),)
        self._index: dict[tuple, list[tuple[str, OutcomeRecord]]] | None = None

    # -- 1-1: the prior query engine -----------------------------------------

    def refresh(self) -> None:
        """Rebuild the in-memory prior index from the ledger. Called
        automatically on first use; call again after new outcomes land.
        (Phase 3 task 3-2 moves this filter into SQL.)"""
        index: dict[tuple, list[tuple[str, OutcomeRecord]]] = {}
        for row in self.memory.beliefs(predicate="outcome"):
            stmt = row["statement"]
            if not is_outcome_statement(stmt):
                continue
            rec = OutcomeRecord.from_statement(stmt)
            index.setdefault((rec.domain, rec.kind), []).append((row["id"], rec))
        self._index = index

    def prior(self, kind: ArtifactKind, target: TargetScope) -> EvidencePrior:
        """The ledger's answer for one gate tuple."""
        if self._index is None:
            self.refresh()
        rows = self._index.get((target.domain, kind.value), [])
        scope_token = target.agent or target.tool or target.skill
        successes = failures = 0
        sessions: set[str] = set()
        citations: list[str] = []
        for aid, rec in rows:
            if target.model_id is not None and rec.model_id != target.model_id:
                continue
            if scope_token and not (rec.chosen and scope_token in rec.chosen):
                continue
            if rec.ok:
                successes += 1
                sessions.add(rec.session_id or "unknown")
            else:
                failures += 1
            if len(citations) < MAX_CITATIONS:
                citations.append(aid)
        return EvidencePrior(successes=successes, failures=failures,
                             sessions=len(sessions), citations=tuple(citations))

    # -- 1-2/1-3: the decision -----------------------------------------------

    def evaluate(self, proposal: Proposal) -> GateVerdict:
        """Compute the verdict WITHOUT transitioning or recording."""
        guards: list[str] = []

        # Constitution / hard blocks come first and are final (I2 of the
        # design note: evidence never bypasses an invariant).
        for block in self.hard_blocks:
            reason = block(proposal)
            if reason is not None:
                return self._verdict(proposal, EvidencePrior(),
                                     GateDecision.AUTO_REJECT,
                                     threshold=threshold_for(0),
                                     guards=("hard_block: " + reason,),
                                     reason=f"hard block: {reason}")
        guards.append("constitution PASS")

        prior = self.prior(proposal.kind, proposal.target)
        thr = threshold_for(prior.successes)
        post = prior.posterior_mean
        guards.append(f"blast={proposal.blast_radius.value}")
        guards.append(f"diversity {prior.sessions}>={self.k_diversity}"
                      if prior.sessions >= self.k_diversity
                      else f"diversity {prior.sessions}<{self.k_diversity}")

        # Cold start: no history for this tuple → a human decides.
        if prior.n == 0:
            return self._verdict(proposal, prior, GateDecision.NEEDS_HUMAN,
                                 threshold=thr, guards=tuple(guards),
                                 reason="cold start: no prior evidence for "
                                        "this (domain, kind, target, model)")

        # Clearly bad, with enough data to say so.
        if prior.n >= MIN_N_FOR_REJECT and post < REJECT_BELOW:
            return self._verdict(proposal, prior, GateDecision.AUTO_REJECT,
                                 threshold=thr, guards=tuple(guards),
                                 reason=f"posterior {post:.2f} < "
                                        f"{REJECT_BELOW} on n={prior.n}")

        # Auto-approve requires: posterior over the tuple's threshold,
        # provenance diversity, and (for wide blast) overwhelming volume.
        if post >= thr:
            if prior.sessions < self.k_diversity:
                return self._verdict(proposal, prior, GateDecision.NEEDS_HUMAN,
                                     threshold=thr, guards=tuple(guards),
                                     reason=f"posterior clears {thr:.2f} but "
                                            f"successes span only "
                                            f"{prior.sessions} session(s) — "
                                            f"diversity floor is "
                                            f"{self.k_diversity}")
            if proposal.blast_radius is BlastRadius.WIDE \
                    and prior.successes < self.wide_min_successes:
                return self._verdict(proposal, prior, GateDecision.NEEDS_HUMAN,
                                     threshold=thr, guards=tuple(guards),
                                     reason=f"wide blast radius needs "
                                            f">={self.wide_min_successes} "
                                            f"successes; has {prior.successes}")
            return self._verdict(proposal, prior, GateDecision.AUTO_APPROVE,
                                 threshold=thr, guards=tuple(guards),
                                 reason=f"posterior {post:.2f} >= threshold "
                                        f"{thr:.2f} with diversity "
                                        f"{prior.sessions}")

        return self._verdict(proposal, prior, GateDecision.NEEDS_HUMAN,
                             threshold=thr, guards=tuple(guards),
                             reason=f"posterior {post:.2f} below threshold "
                                    f"{thr:.2f} — human review")

    # -- 1-4: decide = evaluate + transition + record ------------------------

    def decide(self, proposal: Proposal, *,
               by: str = "gate:evidence") -> tuple[GateVerdict, Proposal]:
        """Evaluate, write the verdict to the ledger (cited), and apply
        the spec transition. Returns (verdict, transitioned proposal)."""
        verdict = self.evaluate(proposal)
        vid = self._record_verdict(proposal, verdict)
        verdict = verdict.model_copy(update={"verdict_assertion_id": vid})

        cites = tuple(
            EvidenceCitation(assertion_id=a, role=EvidenceRole.PRIOR_OUTCOME)
            for a in verdict.prior.citations
        ) + (EvidenceCitation(assertion_id=vid,
                              role=EvidenceRole.CONSTITUTION_CHECK,
                              note="gate verdict"),)

        if verdict.decision is GateDecision.AUTO_APPROVE:
            updated = proposal.with_status(ProposalStatus.APPROVED, by=by,
                                           note=verdict.reason,
                                           extra_evidence=cites)
        elif verdict.decision is GateDecision.AUTO_REJECT:
            updated = proposal.with_status(ProposalStatus.REJECTED, by=by,
                                           note=verdict.reason)
        else:
            updated = proposal.with_status(ProposalStatus.NEEDS_HUMAN, by=by,
                                           note=verdict.reason) \
                if proposal.status is ProposalStatus.PENDING else proposal
        return verdict, updated

    def why(self, proposal_id: str) -> str:
        """Render the latest recorded verdict for a proposal — a ledger
        lookup, not forensics."""
        rows = self.memory.beliefs(subject=proposal_id,
                                   predicate="gate_verdict")
        if not rows:
            return f"no gate verdict recorded for {proposal_id}"
        latest = rows[-1]
        data = json.loads(latest["statement"])
        verdict = GateVerdict.model_validate(data)
        return verdict.render()

    # -- internal ------------------------------------------------------------

    def _verdict(self, proposal: Proposal, prior: EvidencePrior,
                 decision: GateDecision, *, threshold: float,
                 guards: tuple[str, ...], reason: str) -> GateVerdict:
        return GateVerdict(
            proposal_id=proposal.id,
            decision=decision,
            posterior=round(prior.posterior_mean, 4),
            threshold=round(threshold, 4),
            prior=prior,
            domain=proposal.target.domain,
            kind=proposal.kind.value,
            blast_radius=proposal.blast_radius.value,
            diversity_required=self.k_diversity,
            reason=reason,
            guards=guards,
        )

    def _record_verdict(self, proposal: Proposal,
                        verdict: GateVerdict) -> str:
        """The gate's own decision becomes a cited ledger assertion —
        the audit trail of the governance system is produced by the
        governance system, by construction."""
        return self.memory._client.observe(
            verdict.model_dump_json(),
            subject=proposal.id,
            predicate="gate_verdict",
            object=verdict.decision.value,
            confidence=1.0,
            source_id="evidence-gate",
            derived_from=verdict.prior.citations,
        )


def _constitution_block(constitution) -> HardBlock:
    """Adapt a graxella Constitution into a hard block: any invariant
    violation on the proposal payload is a final rejection."""
    def block(proposal: Proposal) -> Optional[str]:
        violations = constitution.check_invariants(
            proposal.payload, applies_to="promotion")
        if violations:
            first = violations[0]
            return getattr(first, "rule_id", None) or str(first)
        return None
    return block


__all__ = [
    "EvidenceGate", "EvidencePrior", "GateVerdict", "GateDecision",
    "threshold_for", "THR_FLOOR", "THR_SPAN", "THR_HALF",
    "REJECT_BELOW", "MIN_N_FOR_REJECT", "WIDE_MIN_SUCCESSES",
]
