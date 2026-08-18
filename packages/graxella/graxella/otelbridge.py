"""graxella.otelbridge — OTel GenAI semantic-convention emission (task 3-3).

Ride the standard, own the evidence: graxella emits ONE ``invoke_agent``
span per dispatch using the ``gen_ai.*`` conventions the APM world
already consumes (Datadog, Jaeger, et al.), and adds the decision layer
no generic APM has — decision id, domain, routing score, ok — as
``graxella.*`` attributes on the same span.

Opt-in and dependency-light: ``enable(app)`` requires opentelemetry-api
(raise loudly if missing — no silent no-op); everything else in graxella
runs without it. Spans are emitted post-hoc with the measured dispatch
timings, so the hot path gains no OTel machinery.
"""
from __future__ import annotations

import time
from typing import Any


def enable(app: Any, *, tracer: Any = None) -> Any:
    """Attach an OTel tracer to an InstrumentedApp. Returns the app."""
    if tracer is None:
        try:
            from opentelemetry import trace
        except ImportError as exc:
            raise ImportError(
                "graxella.otelbridge.enable() needs opentelemetry-api. "
                "Install with: pip install graxella[otel]  (or "
                "opentelemetry-sdk for a full local pipeline)."
            ) from exc
        tracer = trace.get_tracer("graxella")
    app._otel_tracer = tracer
    return app


def emit_dispatch_span(app: Any, *, task: str, result: Any,
                       decision_id: str, domain: str, ok: bool,
                       latency_ms: float | None) -> None:
    """One gen_ai invoke_agent span for a completed dispatch (post-hoc,
    with real timings). Called by InstrumentedApp.route() when enabled."""
    tracer = getattr(app, "_otel_tracer", None)
    if tracer is None:
        return
    end_ns = time.time_ns()
    start_ns = end_ns - int((latency_ms or 0.0) * 1_000_000)
    span = tracer.start_span(
        f"invoke_agent {result.chosen_agent or 'unroutable'}",
        start_time=start_ns,
    )
    try:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.agent.name", result.chosen_agent or "")
        if app.model_id:
            span.set_attribute("gen_ai.request.model", app.model_id)
        if result.tokens_in is not None:
            span.set_attribute("gen_ai.usage.input_tokens", result.tokens_in)
        if result.tokens_out is not None:
            span.set_attribute("gen_ai.usage.output_tokens", result.tokens_out)
        # The decision layer generic APM cannot see:
        span.set_attribute("graxella.decision_id", decision_id)
        span.set_attribute("graxella.domain", domain)
        span.set_attribute("graxella.route.score", float(result.score))
        span.set_attribute("graxella.route.skill", result.chosen_skill or "")
        span.set_attribute("graxella.ok", ok)
        span.set_attribute("graxella.task_head", task[:120])
    finally:
        span.end(end_time=end_ns)


__all__ = ["enable", "emit_dispatch_span"]
