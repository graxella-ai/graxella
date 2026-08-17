"""graxella.tracer.unified — one ordered event stream, three source-of-truth systems.

An operator running an instrumented graph needs to answer questions like:

  * "For user request R, which routing decision fired, which agent ran,
    what did it write to memory, and did any governance detector flag it?"
  * "For belief B, which routing event created it, and which downstream
    rule now depends on it?"

Those questions cross three audit surfaces:

  * agent2society SessionTracer / JsonlFileStore  (routing decisions)
  * mnema WAL                                     (belief writes)
  * graxella ExperienceEpisode                    (LangGraph node runs)

The UnifiedTracer records events from all three via a single append-only
in-memory ring (later: pluggable persistent sink) and provides ``timeline``
and ``why`` queries that join across the sources.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Optional


@dataclass(frozen=True)
class TraceEvent:
    """One row in the unified audit trail."""

    seq: int
    ts: float
    source: str            # "society" | "beliefs" | "episode" | "gate" | "constitution"
    event_type: str        # e.g. "route" | "decision" | "outcome" | "governance.conflict"
    payload: dict


class UnifiedTracer:
    """Thread-safe append-only tracer with joint query methods.

    Beat 1: in-memory ring. Beat 1.5: pluggable persistent sink (JSONL /
    SQLite / OTLP).
    """

    def __init__(self, *, max_events: int = 10_000) -> None:
        self._events: list[TraceEvent] = []
        self._seq = itertools.count(1)
        self._lock = Lock()
        self._max_events = max_events

    # -- construction helpers ------------------------------------------------

    @classmethod
    def default(cls) -> "UnifiedTracer":
        return cls()

    def hook_for(self, source: str):
        """Return a ``(event_type, payload) -> None`` callable bound to ``source``.

        Use this to wire ``Memory`` / ``Society`` (and later, gate/constitution)
        into the same tracer instance without leaking the tracer object into
        those adapters.
        """
        def _hook(event_type: str, payload: dict) -> None:
            self.record(source, event_type, payload)
        return _hook

    # -- append --------------------------------------------------------------

    def record(self, source: str, event_type: str, payload: dict) -> TraceEvent:
        with self._lock:
            evt = TraceEvent(
                seq=next(self._seq),
                ts=time.time(),
                source=source,
                event_type=event_type,
                payload=dict(payload),
            )
            self._events.append(evt)
            if len(self._events) > self._max_events:
                # ring behaviour — drop oldest
                self._events = self._events[-self._max_events:]
            return evt

    # -- read ----------------------------------------------------------------

    def events(self, *, source: str | None = None,
               event_type: str | None = None,
               limit: int | None = None) -> list[TraceEvent]:
        with self._lock:
            out = list(self._events)
        if source is not None:
            out = [e for e in out if e.source == source]
        if event_type is not None:
            out = [e for e in out if e.event_type == event_type]
        if limit is not None:
            out = out[-limit:]
        return out

    def timeline(self, *, handoff_id: str | None = None,
                 assertion_id: str | None = None) -> list[TraceEvent]:
        """Return events related to a given routing decision or belief.

        Correlation is by payload field: ``handoff_id`` for routing chains,
        ``assertion_id`` for belief chains, ``decision_id`` for outcomes
        pointing back to a decision.
        """
        out: list[TraceEvent] = []
        for evt in self.events():
            p = evt.payload
            if handoff_id is not None and p.get("handoff_id") == handoff_id:
                out.append(evt)
                continue
            if assertion_id is not None:
                if p.get("assertion_id") == assertion_id:
                    out.append(evt)
                    continue
                if p.get("decision_id") == assertion_id:
                    out.append(evt)
                    continue
        return out

    def why(self, assertion_id: str, *, memory: Optional[Any] = None) -> dict:
        """Cross-source ``why_believed`` — union of tracer chain and mnema audit.

        If ``memory`` is provided, its ``why(assertion_id)`` is included so
        the caller sees mnema's full provenance DAG alongside the routing
        events that produced the belief.
        """
        chain = [_as_row(e) for e in self.timeline(assertion_id=assertion_id)]
        mnema_view: dict | None = None
        if memory is not None:
            try:
                mnema_view = memory.why(assertion_id)
            except Exception as exc:
                mnema_view = {"error": repr(exc)}
        return {
            "assertion_id": assertion_id,
            "tracer_chain": chain,
            "mnema": mnema_view,
        }


def _as_row(e: TraceEvent) -> dict:
    return {
        "seq": e.seq,
        "ts": e.ts,
        "source": e.source,
        "event_type": e.event_type,
        "payload": e.payload,
    }
