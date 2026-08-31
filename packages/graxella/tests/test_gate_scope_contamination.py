"""Regression: gate evidence must NOT leak across tuples on a name prefix.

External probe (2026-08-30): 30 successful transform outcomes recorded for
tool `search_flights_v2` were inherited by an unrelated tool named `search`
via substring scope matching -- posterior 0.969, enough to auto-approve a
transform for a tool that never executed once. The scope match is now
exact-token (split on "::", require equality).
"""
from __future__ import annotations

from graxella.beliefs import Memory
from graxella.gate.evidence import EvidenceGate
from graxella.gate.spec import ArtifactKind, TargetScope


def _seed(mem: Memory, tool: str, n: int) -> None:
    for i in range(n):
        aid = mem.record_decision(decision_type="tool", task="call",
                                  chosen=tool, domain="travel")
        mem.record_outcome(decision_id=aid, ok=True, kind="transform",
                           chosen=tool, domain="travel",
                           session_id=f"s{i}")


def test_prefix_tool_inherits_nothing():
    mem = Memory.sqlite(":memory:", agent_id="t", namespace="travel")
    _seed(mem, "search_flights_v2", 30)
    gate = EvidenceGate(mem)

    # the unrelated short-named tool must start COLD
    prior = gate.prior(ArtifactKind.TRANSFORM,
                       TargetScope(domain="travel", tool="search"))
    assert prior.n == 0, f"evidence leaked: {prior.n} outcomes inherited"
    assert prior.sessions == 0

    # and the real tool keeps its own evidence intact
    real = gate.prior(ArtifactKind.TRANSFORM,
                      TargetScope(domain="travel", tool="search_flights_v2"))
    assert real.successes == 30
    assert real.sessions == 30


def test_composite_chosen_still_matches_agent_scope():
    """Delegate outcomes record chosen='agent::skill'; an agent-scoped
    prior must still find them via exact segment equality."""
    mem = Memory.sqlite(":memory:", agent_id="t", namespace="support")
    for i in range(3):
        aid = mem.record_decision(decision_type="delegate", task="x",
                                  chosen="refunds::refund_skill",
                                  domain="support")
        mem.record_outcome(decision_id=aid, ok=True, kind="delegate",
                           chosen="refunds::refund_skill", domain="support",
                           session_id=f"s{i}")
    gate = EvidenceGate(mem)
    prior = gate.prior(ArtifactKind.RULE if hasattr(ArtifactKind, "RULE")
                       else list(ArtifactKind)[0],
                       TargetScope(domain="support", agent="refunds"))
    # kind must match the recorded kind for the index lookup:
    from graxella.gate.spec import ArtifactKind as AK
    kinds = {k.value: k for k in AK}
    if "delegate" in kinds:
        prior = gate.prior(kinds["delegate"],
                           TargetScope(domain="support", agent="refunds"))
        assert prior.successes == 3
    # exact segment match must also reject a prefix of the agent name
    if "delegate" in kinds:
        leak = gate.prior(kinds["delegate"],
                          TargetScope(domain="support", agent="refund"))
        assert leak.n == 0
