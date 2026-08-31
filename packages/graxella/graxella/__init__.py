"""Graxella — graph-native agent intelligence layer.

Every agent execution becomes a cited, trust-graded, replayable Experience
row. Offline, the agenda mines those rows and proposes graph mutations for
human review. Approved rules take effect at the next dispatch — no restart,
no re-deploy, no runtime LLM.

Sklearn-style layout — each subpackage owns one concept:

    graxella.memory         Experience episodes + durable stores
    graxella.knowledge      Turn documentation into a KnowledgeSeed
    graxella.rulebook       Approved graph mutations, on disk
    graxella.agenda         Offline miners + runner + Proposal
    graxella.reasoning      Bottom-up Datalog engine (compile-time)
    graxella.healing        Drift → deterministic self-heal dispatch
    graxella.discovery      Semantic tool catalog (B8 AXON)
    graxella.routing        Deterministic destination picker (B11)
    graxella.audit          PROV-O JSON-LD provenance export
    graxella.integrations   Host adapters (langgraph, axon, mcp)
    graxella.cli            `graxella` console entry point

The common conveniences are re-exported here so `from graxella import X`
works for the top ~10 things everyone reaches for.
"""
from graxella import _workspace as _workspace  # noqa: F401  # bootstrap sibling paths
from graxella._version import __version__
from graxella.agenda import (CapabilityReweigher, DocsMiner, HiddenAgendaRunner,
                             Proposal, RuleDistiller, TrustPromoter)
from graxella.audit import export as audit_export
from graxella.exceptions import (GraxellaError, ProposalNotFoundError,
                                 RulebookError, UnsafeRuleError)
from graxella.healing import (GraxellaApp, HealedTool, ToolInterceptor,
                              TransformRecipe, heal_wrap, tool_trust, wrap,
                              wrap_tools)
from graxella.knowledge import Assertion, KnowledgeSeed, from_docs
from graxella.memory import (ExperienceEpisode, ExperienceStore,
                             InMemoryExperienceStore, SqliteExperienceStore,
                             ToolCall)
from graxella.rulebook import ApprovedRule, Rulebook

# Beat 1 orchestration surface — wraps LangGraph / callables with memory,
# routing, tracing, and gated learning in one call.
from graxella.beliefs import Memory
from graxella.constitution import Constitution, Violation
from graxella.gate import (EvidenceGate, EvidencePrior, GateVerdict,
                            GateDecision, GatePolicy, ObjectiveScores,
                            PromotionGate, Proposal as GateProposal,
                            ProposalStatus, spec as promotion_spec)
from graxella.gate.spec import Proposal as PromotionProposal
from graxella.agent import Agent
from graxella.api.topology import render_html, topology_data
from graxella.gate.evidence import pending_from_ledger
from graxella.instrument import InstrumentedApp, instrument
from graxella.mesh import mesh, supervisor
from graxella.session import ReconcileResult, Session
from graxella.society import Society
from graxella.tracer import TraceEvent, UnifiedTracer
from graxella.trajectory import Hop, TrajectoryBudget, TrajectoryResult

__all__ = [
    "__version__",
    # memory
    "ExperienceEpisode",
    "ExperienceStore",
    "InMemoryExperienceStore",
    "SqliteExperienceStore",
    "ToolCall",
    # knowledge
    "Assertion",
    "KnowledgeSeed",
    "from_docs",
    # rulebook
    "ApprovedRule",
    "Rulebook",
    # agenda
    "Proposal",
    "RuleDistiller",
    "CapabilityReweigher",
    "TrustPromoter",
    "DocsMiner",
    "HiddenAgendaRunner",
    # healing
    "TransformRecipe",
    "HealedTool",
    "ToolInterceptor",
    "heal_wrap",
    "tool_trust",
    "wrap",
    "wrap_tools",
    "GraxellaApp",
    # audit
    "audit_export",
    # exceptions
    "GraxellaError",
    "RulebookError",
    "ProposalNotFoundError",
    "UnsafeRuleError",
    # Beat 1 orchestration surface
    "Agent",
    "Session",
    "ReconcileResult",
    "mesh",
    "supervisor",
    "instrument",
    "InstrumentedApp",
    "Memory",
    "Society",
    "UnifiedTracer",
    "TraceEvent",
    "EvidenceGate",
    "EvidencePrior",
    "GateVerdict",
    "PromotionProposal",
    "promotion_spec",
    "PromotionGate",
    "GateProposal",
    "ProposalStatus",
    "GatePolicy",
    "GateDecision",
    "ObjectiveScores",
    "Constitution",
    "Violation",
    "pending_from_ledger",
    # trajectories
    "TrajectoryBudget",
    "TrajectoryResult",
    "Hop",
    # topology map
    "topology_data",
    "render_html",
]
