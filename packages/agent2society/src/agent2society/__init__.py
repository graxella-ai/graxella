"""agent2society -- the transparent coordination layer for A2A agent meshes.

Sits on top of A2A. Replaces opaque LLM-negotiated coordination with a
deterministic, graph-routed dispatch that produces a human-readable
explanation for every decision -- which agent was chosen, the features
that drove the score, the alternatives that were rejected and why, and
the chosen agent's self-declared limits.

The public surface, in three layers:

  1. Routing + dispatch (v1)
       Mesh, AgentCard, Skill, CapabilityGraph, Router, RouteCandidate,
       Transports, conformance_check.

  2. Meaning + context (v1.5)
       Handoff, DecisionRecord -- the envelope that carries intent,
       assumptions, and an upstream decision chain alongside the task.
       SelfAssessment -- an agent's declared limits, confidence model,
       and escalation triggers.

  3. Transparency + governance (v1.5)
       RoutingExplanation, Conflict, CapabilityDrift, plus hooks on the
       Mesh: on_conflict, on_low_confidence, on_capability_drift,
       on_human_review. Detection-only -- never auto-corrects.
"""
from __future__ import annotations

from .card import AgentCard, SelfAssessment, Skill, load_card, parse_card
from .conformance import ConformanceResult, check as conformance_check
from .dispatcher import (
    CompositeTransport,
    HttpTransport,
    LocalTransport,
    Transport,
    build_message_send_payload,
    extract_text,
)
from .embeddings import EmbedFn, TfidfEmbedder
from .exceptions import (
    AgentCardError,
    ConformanceViolation,
    DispatchError,
    SocietyError,
    NoRouteError,
)
from .explanation import RoutingExplanation, build_rationale
from .governance import (
    CapabilityDrift,
    CapabilityDriftDetector,
    Conflict,
    ConflictDetector,
    GovernanceHooks,
)
from .graph import AgentNode, Boundary, CapabilityGraph
from .handoff import DecisionRecord, Handoff
from .mesh import Mesh
from .metrics import MetricsCollector
from .optimizer import LLMSuggestFn, OptimizationReport, SkillEdit
from .router import RouteCandidate, Router
from .store import ExplanationStore, InMemoryStore, JsonlFileStore
from .trace import SessionTracer, TraceEvent

# Primary public name. Mesh stays as a back-compat alias for users who
# adopted the v0 surface; the canonical class is Society.
Society = Mesh

__version__ = "0.5.3"

__all__ = [
    "__version__",
    # core facade
    "Society",
    "Mesh",
    # cards + graph
    "AgentCard",
    "Skill",
    "SelfAssessment",
    "CapabilityGraph",
    "AgentNode",
    "Boundary",
    "load_card",
    "parse_card",
    # routing
    "Router",
    "RouteCandidate",
    # conformance
    "ConformanceResult",
    "conformance_check",
    # transport
    "Transport",
    "HttpTransport",
    "LocalTransport",
    "CompositeTransport",
    "build_message_send_payload",
    "extract_text",
    # embeddings
    "EmbedFn",
    "TfidfEmbedder",
    # v1.5 meaning + context
    "Handoff",
    "DecisionRecord",
    # v1.5 transparency
    "RoutingExplanation",
    "build_rationale",
    # v1.5 governance
    "Conflict",
    "CapabilityDrift",
    "ConflictDetector",
    "CapabilityDriftDetector",
    "GovernanceHooks",
    # v0.4 observation-driven optimizer
    "OptimizationReport",
    "SkillEdit",
    # v0.5 production ops
    "MetricsCollector",
    "ExplanationStore",
    "InMemoryStore",
    "JsonlFileStore",
    "LLMSuggestFn",
    # v0.5.3 observability
    "SessionTracer",
    "TraceEvent",
    # exceptions
    "SocietyError",
    "AgentCardError",
    "NoRouteError",
    "ConformanceViolation",
    "DispatchError",
]
