"""Task 3-3 — OTel GenAI emission: standard spans, graxella attributes."""
from __future__ import annotations

import pytest

opentelemetry = pytest.importorskip("opentelemetry.sdk")
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

import graxella  # noqa: E402
from graxella.beliefs import Memory  # noqa: E402
from graxella.otelbridge import enable  # noqa: E402


def billing_agent(payload):
    """decide refund eligibility for billing complaints and orders"""
    return {"result": f"handled {payload}",
            "usage": {"input_tokens": 11, "output_tokens": 4}}


def test_dispatch_emits_gen_ai_span_with_decision_layer(tmp_path):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("graxella-test")

    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="otel",
                           namespace="refunds")
    app = graxella.mesh([billing_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"),
                        domain="refunds", model_id="stub-llm", recall=False)
    enable(app, tracer=tracer)
    _, aid = app.route("billing refund order 7")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "invoke_agent billing_agent"
    a = dict(span.attributes)
    # Standard gen_ai.* — the schema APMs already consume.
    assert a["gen_ai.operation.name"] == "invoke_agent"
    assert a["gen_ai.agent.name"] == "billing_agent"
    assert a["gen_ai.request.model"] == "stub-llm"
    assert a["gen_ai.usage.input_tokens"] == 11
    assert a["gen_ai.usage.output_tokens"] == 4
    # The graxella decision layer generic APM cannot see.
    assert a["graxella.decision_id"] == aid
    assert a["graxella.domain"] == "refunds"
    assert a["graxella.ok"] is True
    assert a["graxella.route.score"] > 0
    assert span.end_time >= span.start_time


def test_otel_is_opt_in(tmp_path):
    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="otel2")
    app = graxella.mesh([billing_agent], memory=memory,
                        store_path=str(tmp_path / "r.jsonl"), recall=False)
    app.route("billing refund order 8")     # no enable(): must not raise
