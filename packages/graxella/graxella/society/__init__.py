"""graxella.society — deterministic mesh routing + governance.

Wraps the vendored ``agent2society`` runtime (TF-IDF / embedding router,
Handoff envelope, conformance boundaries, governance detectors) into a
graxella-shaped API. Every routing decision made by an instrumented graph
flows through this layer, is judged by the governance detectors, and is
recorded through the unified tracer into the belief store.
"""
from graxella.society.adapter import Society

__all__ = ["Society"]
