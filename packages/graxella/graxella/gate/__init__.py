"""graxella.gate — the promotion gate.

Every learned strategy (a mined rule, a distilled skill, a promoted route)
enters the gate as a pending proposal. The gate scores each proposal by
confidence and blast radius; a proposal only becomes active policy after
crossing an approval threshold or receiving explicit human sign-off.

The gate is the differentiator vs. every academic learning system —
ReasoningBank, GPTSwarm, MasRouter, Router-R1 all promote silently. No
enterprise buys silent promotion.

Canonical schema: ``graxella.gate.spec`` (Promotion Spec v0.1,
docs/specs/promotion-spec.md) — ONE Proposal type for every learnable
artifact. The ``promoter`` module below is the INTERIM scored gate; its
``Proposal``/``GatePolicy`` are deprecated and are replaced by the
mnema-grounded Evidence Gate in Phase 1 (build plan task 1-5).
"""
from graxella.gate import spec as spec  # canonical Promotion Spec models
from graxella.gate.promoter import (  # interim — deprecated, removed Phase 1
    GateDecision,
    GatePolicy,
    ObjectiveScores,
    Proposal,
    ProposalStatus,
    PromotionGate,
)

__all__ = [
    "spec",
    "PromotionGate", "Proposal", "ProposalStatus",
    "GateDecision", "GatePolicy", "ObjectiveScores",
]
