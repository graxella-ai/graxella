"""graxella.gate.health — un-learning: does a PROMOTED rule still earn its
keep?

The Evidence Gate (evidence.py) decides whether a PROPOSAL should become a
rule. This module is its mirror image: it decides whether an ACTIVE rule
should STOP being one. Same math (Beta-Bernoulli posterior), same
discipline (zero LLM, cited, deterministic) — deliberately asymmetric
thresholds, because the two decisions carry opposite costs:

  * promotion's false-positive cost = a bad rule starts serving traffic.
    Guard hard: cold-start NEEDS_HUMAN, high threshold, diversity floor.
  * demotion's false-negative cost = a bad rule KEEPS serving traffic
    while its damage compounds on every call. React fast: a small,
    recent sample of majority failures is enough to pull it, because the
    downside of an unnecessary demotion (the healer/human simply re-earns
    the rule from scratch) is far cheaper than the downside of leaving a
    harmful rule active.

Evidence source: rule-scoped ``touched`` edges the heal interceptor writes
on every rung-2 dispatch of a specific rule (``role="rule_use"``,
``detail={"ok": bool}``) — an exact-id tally, independent of the gate's
tool-scoped aggregate (which mixes evidence across every rule that has
ever targeted that tool, including superseded ones; that aggregate is
right for judging a NEW proposal, wrong for judging THIS rule's own
recent behavior).
"""
from __future__ import annotations

from dataclasses import dataclass

from graxella.beliefs.adapter import Memory

#: React fast: 3 uses is enough to judge an ACTIVE rule (vs. the gate's
#: MIN_N_FOR_REJECT=5 for a merely-pending proposal).
DEMOTE_MIN_N = 3
#: Demote once the Beta-Bernoulli posterior for "this rule succeeds"
#: drops below even odds — no benefit-of-the-doubt for a rule already
#: causing failures in production.
DEMOTE_BELOW = 0.50


@dataclass(frozen=True)
class RuleHealth:
    rule_id: str
    successes: int
    failures: int

    @property
    def n(self) -> int:
        return self.successes + self.failures

    @property
    def posterior(self) -> float:
        return (self.successes + 1.0) / (self.n + 2.0)


def rule_health(memory: Memory, rule_id: str) -> RuleHealth:
    """Tally every recorded rung-2 use of ``rule_id`` (exact id, no
    cross-rule bleed — the same lesson P0-1 fixed for the gate)."""
    successes = failures = 0
    for row in memory.touching(rule_id):
        if row.get("role") != "rule_use":
            continue
        if row.get("ok"):
            successes += 1
        else:
            failures += 1
    return RuleHealth(rule_id=rule_id, successes=successes, failures=failures)


def should_demote(health: RuleHealth) -> tuple[bool, str]:
    """(demote?, cited reason). Deterministic, evidence-only — the same
    "posterior vs. threshold, guarded by a minimum-evidence floor" shape
    as the promotion gate, tuned for demotion's faster-reaction posture."""
    if health.n < DEMOTE_MIN_N:
        return False, (f"only {health.n} recorded use(s) — below the "
                       f"{DEMOTE_MIN_N}-use floor for a demotion decision")
    if health.posterior < DEMOTE_BELOW:
        return True, (f"posterior {health.posterior:.2f} < {DEMOTE_BELOW} "
                      f"over {health.n} uses "
                      f"({health.successes} ok / {health.failures} failed)")
    return False, (f"posterior {health.posterior:.2f} >= {DEMOTE_BELOW} "
                   f"over {health.n} uses — still earning its keep")


__all__ = ["RuleHealth", "rule_health", "should_demote",
           "DEMOTE_MIN_N", "DEMOTE_BELOW"]
