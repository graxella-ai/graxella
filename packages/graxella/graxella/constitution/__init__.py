"""graxella.constitution — declared invariants, structurally enforced.

A Constitution is a JSON Schema document declaring: cost budgets, latency
SLAs, side-effect classes an agent may not perform, compliance predicates
that must hold at every step, and a determinism budget the workflow must
respect. Predicates are compiled at construction time; ``check_*`` calls
run pre-compiled validators.

Enforcement is detection-only: violations are surfaced through the tracer
as ``governance.constitution_violation`` events and never block dispatch.
"""
from graxella.constitution.schema import (
    CONSTITUTION_META_SCHEMA,
    Constitution,
    Violation,
)

__all__ = ["Constitution", "Violation", "CONSTITUTION_META_SCHEMA"]
