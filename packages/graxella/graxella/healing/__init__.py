"""graxella.healing — drift → self-heal dispatch.

Two layers:

  * `recipes`     — deterministic arg translation (TransformRecipe) and a
                    primary/fallback wrapper (HealedTool).
  * `dispatch`    — rulebook-aware routing that consults the Rulebook
                    BEFORE the primary runs (`wrap_tools`, `wrap`,
                    GraxellaApp).
  * `interceptor` — the heal ladder around one tool (ToolInterceptor):
                    happy path → promoted transform → heal-once → loud
                    failure ("fail once, learn forever").
  * `trust`       — cited, Laplace-smoothed tool trust from the outcome
                    ledger (tool_trust, preferred).

Public surface:
    TransformRecipe                — the arg-translation shape
    HealedTool, heal_wrap          — primary/fallback wrapper
    wrap_tools, wrap, GraxellaApp  — rulebook-consulting dispatch
    ToolInterceptor, Healer        — the heal ladder, gate-governed
    is_drift, DRIFT_SIGNATURE      — the drift signature
    ToolTrust, tool_trust, preferred — cited tool trust + failover order
"""
from graxella.healing.dispatch import GraxellaApp, wrap, wrap_tools
from graxella.healing.dspy_healer import build_default_healer
from graxella.healing.interceptor import (DRIFT_SIGNATURE, Healer,
                                          ToolInterceptor, classify_drift,
                                          is_drift)
from graxella.healing.recipes import HealedTool, TransformRecipe
from graxella.healing.recipes import route as heal_route
from graxella.healing.recipes import wrap as heal_wrap
from graxella.healing.trust import ToolTrust, preferred, tool_trust

# Promotion Spec wiring (S-1): healing recipes ship as spec.Proposal
# (kind=transform) through the gate from Phase 2 (task 2-5); the canonical
# schema is imported here so the migration target is explicit.
from graxella.gate import spec as promotion_spec  # noqa: F401

__all__ = [
    "DRIFT_SIGNATURE",
    "build_default_healer",
    "classify_drift",
    "GraxellaApp",
    "HealedTool",
    "Healer",
    "ToolInterceptor",
    "ToolTrust",
    "TransformRecipe",
    "heal_route",
    "heal_wrap",
    "is_drift",
    "preferred",
    "tool_trust",
    "wrap",
    "wrap_tools",
]
