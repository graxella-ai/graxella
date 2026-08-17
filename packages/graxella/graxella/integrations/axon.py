"""Transparent instrumentation of axon_runtime.Runtime.dispatch().

The single hook. Wraps Runtime.dispatch() so every call transparently produces
one ExperienceEpisode written to the store, without requiring any change to
agent code, node code, or the runtime itself.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

from graxella.memory.episode import ExperienceEpisode, ExperienceStore, ToolCall

if TYPE_CHECKING:
    from axon_runtime.runtime import Runtime


def _args_hash(payload: dict[str, Any]) -> str:
    try:
        canon = json.dumps(payload, sort_keys=True, default=str)
    except TypeError:
        canon = repr(payload)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def attach(runtime: "Runtime", store: ExperienceStore) -> "Runtime":
    """Monkey-patch runtime.dispatch to record ExperienceEpisodes into store.

    Idempotent: attaching twice is a no-op (the runtime carries a marker).
    Returns the same runtime for fluent chaining.

    The wrapper is deliberately additive — the original dispatch runs
    unchanged, then we extract the outcome + decisions and shape the
    Experience row. Zero cost when store is a no-op.
    """
    if getattr(runtime, "_graxella_attached", False):
        return runtime

    original = runtime.dispatch

    def instrumented(task: str, **kw: Any) -> Any:
        started = time.time()
        payload = kw.get("payload", {}) or {}
        intent = kw.get("intent", "query")
        skill = kw.get("skill")
        side_effect = kw.get("side_effect")
        side_effect_class = getattr(side_effect, "value", "read_only")

        # --- run the real dispatch ------------------------------------------
        result = original(task, **kw)

        # --- shape the Experience row ---------------------------------------
        episode = ExperienceEpisode(
            session_id=result.envelope.session_id,
            envelope_id=result.envelope.envelope_id,
            intent=intent,
            task=task,
            skill=skill,
            side_effect_class=side_effect_class,
            ok=result.ok,
            err=result.reason or None,
            latency_ms=(time.time() - started) * 1000,
            started_at=started,
            ended_at=time.time(),
        )

        # Routing decision -> chosen agent + margin + flags
        route = next((d for d in result.decisions
                      if getattr(d.kind, "value", "") == "route"), None)
        if route is not None:
            episode.agent_uri = route.chosen or ""
            episode.routing_margin = route.margin
            episode.routing_flags = sorted(route.flags)

        # Every decision id gets recorded
        episode.decisions = [
            f"{getattr(d.kind, 'value', '')}::{d.chosen}"
            for d in result.decisions
        ]

        # Skill/tool footprint — first-cut treats the skill as one tool call
        if skill:
            episode.tool_calls.append(ToolCall(
                tool_id=skill,
                args_hash=_args_hash(payload),
                ok=result.ok,
                latency_ms=episode.latency_ms,
                err=episode.err,
            ))

        store.put(episode)
        return result

    runtime.dispatch = instrumented  # type: ignore[method-assign]
    runtime._graxella_attached = True  # type: ignore[attr-defined]
    runtime._graxella_store = store  # type: ignore[attr-defined]
    return runtime
