"""graxella.healing — drift → self-heal dispatch.

Two layers:

  * `recipes`  — deterministic arg translation (TransformRecipe) and a
                 primary/fallback wrapper (HealedTool).
  * `dispatch` — rulebook-aware routing that consults the Rulebook BEFORE
                 the primary runs (`wrap_tools`, `wrap`, GraxellaApp).

Public surface:
    TransformRecipe                — the arg-translation shape
    HealedTool, heal_wrap          — primary/fallback wrapper
    wrap_tools, wrap, GraxellaApp  — rulebook-consulting dispatch
"""
from graxella.healing.dispatch import GraxellaApp, wrap, wrap_tools
from graxella.healing.recipes import HealedTool, TransformRecipe
from graxella.healing.recipes import route as heal_route
from graxella.healing.recipes import wrap as heal_wrap

# Promotion Spec wiring (S-1): healing recipes ship as spec.Proposal
# (kind=transform) through the gate from Phase 2 (task 2-5); the canonical
# schema is imported here so the migration target is explicit.
from graxella.gate import spec as promotion_spec  # noqa: F401

__all__ = [
    "GraxellaApp",
    "HealedTool",
    "TransformRecipe",
    "heal_route",
    "heal_wrap",
    "wrap",
    "wrap_tools",
]
