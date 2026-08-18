"""graxella.agenda — offline learning that runs behind the visible agents.

The runtime is dumb on purpose. All learning happens here, offline, in a
sandbox that reads a snapshot of the ExperienceStore and emits Proposal
objects. Nothing is ever promoted without human review — the runner
writes proposals.json; a separate approval step moves them to production.

Public surface:
    Proposal
    RuleDistiller, CapabilityReweigher, TrustPromoter  — experience miners
    DocsMiner                                          — docs miner
    DatalogMiner, default_rules                        — reasoning miner
    HiddenAgendaRunner                                 — bundle + dump
"""
from graxella.agenda.datalog_miner import DatalogMiner, default_rules
from graxella.agenda.docs_miner import DocsMiner
from graxella.agenda.miners import (CapabilityReweigher, Proposal,
                                    RuleDistiller, TrustPromoter)
from graxella.agenda.mismatch import MismatchMiner
from graxella.agenda.runner import HiddenAgendaRunner

# Promotion Spec wiring (S-1): miners re-emit spec.Proposal in Phase 1
# (task 1-5); until then the canonical schema is imported here so the
# migration target is explicit. The local ``Proposal`` above is deprecated.
from graxella.gate import spec as promotion_spec  # noqa: F401

__all__ = [
    "Proposal",
    "MismatchMiner",
    "RuleDistiller",
    "CapabilityReweigher",
    "TrustPromoter",
    "DocsMiner",
    "DatalogMiner",
    "default_rules",
    "HiddenAgendaRunner",
]
