"""graxella.beliefs — the belief / memory surface.

Wraps the vendored ``mnema`` runtime (immutable typed assertions, WAL,
sleep-consolidation, retract cascade) into a graxella-shaped API. Every
orchestration decision made by an instrumented graph becomes an
``Assertion`` in the belief store, cited by the unified tracer, and
available to the promotion gate.
"""
from graxella.beliefs.adapter import Memory, best_embedder, embedder_id

__all__ = ["Memory", "best_embedder", "embedder_id"]
