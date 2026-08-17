"""graxella.tracer — unified audit surface.

Joins three event sources into one ordered stream:
  * ``agent2society`` SessionTracer + RoutingExplanation
  * ``mnema`` WAL events (observe / revise / retract / consolidate)
  * ``graxella`` ExperienceEpisode (LangGraph callback captures)

Provides ``why_believed(assertion_id)`` and ``timeline(subject)`` queries
that span all three, so a compliance operator can trace one user request
through routing, execution, memory writes, and rule promotions.
"""
from graxella.tracer.unified import TraceEvent, UnifiedTracer

__all__ = ["UnifiedTracer", "TraceEvent"]
