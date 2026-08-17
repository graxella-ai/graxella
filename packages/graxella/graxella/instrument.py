"""graxella.instrument — the one-line wrap that turns any LangGraph
(or plain callable) into an observed, governed, learning runtime.

Usage:

    from graxella import instrument
    from graxella.beliefs import Memory
    from graxella.society import Society
    from graxella.tracer import UnifiedTracer
    from graxella.constitution import Constitution
    from graxella.gate import PromotionGate

    memory  = Memory.sqlite("./demo.db", agent_id="pipeline_v1")
    society = Society()
    society.add("researcher", research_callable, skills=["research"])
    society.add("writer",     writer_callable,   skills=["writing"])

    wrapped = instrument(
        my_langgraph_app,
        memory=memory,
        society=society,
        # optional in Beat 1:
        constitution=Constitution.empty(),
        gate=PromotionGate(threshold=0.85, require_human=True),
        tracer=UnifiedTracer.default(),
    )

    result = wrapped.invoke({"input": "..."})

    # Query the runtime afterwards:
    wrapped.tracer.events(source="society", event_type="route")
    wrapped.memory.why(some_assertion_id)
    wrapped.gate.pending()

Design notes:

  * ``instrument`` never edits the customer's graph. It attaches
    callbacks + wires tracer hooks. The graph runs unchanged; the wrap
    only *observes* + *governs* + *records*.
  * The returned InstrumentedApp is a thin facade — ``.invoke``,
    ``.stream``, ``.batch`` pass through to the underlying app, threading
    graxella callbacks into the ``config`` at call time.
  * Memory, Society, Gate, Constitution, Tracer are all attributes on
    the returned object, so operators can query them at any point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from graxella.beliefs import Memory
from graxella.constitution import Constitution
from graxella.gate import PromotionGate
from graxella.integrations.langgraph import GraxellaCallback
from graxella.memory.episode import ExperienceStore, InMemoryExperienceStore
from graxella.society import Society
from graxella.tracer import UnifiedTracer


@dataclass
class InstrumentedApp:
    """The object returned by ``instrument(...)``.

    Forwards ``invoke`` / ``stream`` / ``batch`` to the wrapped app while
    threading graxella callbacks into every call.
    """

    app: Any
    memory: Memory
    society: Society
    tracer: UnifiedTracer
    gate: PromotionGate
    constitution: Constitution
    episode_store: ExperienceStore
    _callback: GraxellaCallback = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._callback = GraxellaCallback(store=self.episode_store)

    # -- run methods --------------------------------------------------------

    def invoke(self, input_: Any, config: dict | None = None) -> Any:
        cfg = self._merge_callbacks(config)
        return self.app.invoke(input_, cfg) if _accepts_config(self.app.invoke) \
            else self.app.invoke(input_)

    def stream(self, input_: Any, config: dict | None = None):
        cfg = self._merge_callbacks(config)
        if _accepts_config(getattr(self.app, "stream", None)):
            return self.app.stream(input_, cfg)
        return self.app.stream(input_)

    def batch(self, inputs: list[Any], config: dict | None = None) -> list[Any]:
        cfg = self._merge_callbacks(config)
        if _accepts_config(getattr(self.app, "batch", None)):
            return self.app.batch(inputs, cfg)
        return self.app.batch(inputs)

    # -- operator convenience ----------------------------------------------

    def route(self, task: str, **kwargs) -> Any:
        """Convenience: route a task through the mesh, recording the
        decision in memory + tracer in one step. Also runs the
        Constitution's invariant + budget checks on the produced
        RouteResult and surfaces any violations as detection-only
        tracer events."""
        result = self.society.route(task, **kwargs)
        aid = self.memory.record_decision(
            decision_type="delegate",
            task=task,
            chosen=f"{result.chosen_agent}::{result.chosen_skill}",
            rationale=result.rationale,
            confidence=result.score,
        )
        self._enforce_constitution(
            applies_to="delegate",
            output={
                "chosen_agent": result.chosen_agent,
                "chosen_skill": result.chosen_skill,
                "score": result.score,
                "margin": result.margin,
                "flags": list(result.flags),
                "response": result.response,
            },
            decision_id=aid,
        )
        return result, aid

    def check_constitution(self, *,
                           output: Any = None,
                           usage: dict | None = None,
                           trajectory: list[str] | None = None,
                           side_effect_class: str | None = None,
                           applies_to: str | None = None,
                           decision_id: str | None = None) -> list:
        """Run every applicable Constitution check and emit one tracer
        event per violation. Returns the raw Violation list so callers can
        take domain-specific action (log, alert, hold for review)."""
        violations = self.constitution.check(
            output=output, usage=usage, trajectory=trajectory,
            side_effect_class=side_effect_class, applies_to=applies_to,
        )
        for v in violations:
            self.tracer.record(
                "constitution", "governance.constitution_violation",
                {**v.to_dict(), "decision_id": decision_id},
            )
        return violations

    def why(self, assertion_id: str) -> dict:
        return self.tracer.why(assertion_id, memory=self.memory)

    # -- internal -----------------------------------------------------------

    def _enforce_constitution(self, *, applies_to: str, output: Any,
                              decision_id: str | None) -> None:
        for v in self.constitution.check_invariants(output, applies_to=applies_to):
            self.tracer.record(
                "constitution", "governance.constitution_violation",
                {**v.to_dict(), "decision_id": decision_id},
            )

    def _merge_callbacks(self, config: dict | None) -> dict:
        cfg = dict(config or {})
        cbs = list(cfg.get("callbacks") or [])
        if self._callback not in cbs:
            cbs.append(self._callback)
        cfg["callbacks"] = cbs
        return cfg


def instrument(app: Any, *,
               memory: Memory,
               society: Society,
               tracer: UnifiedTracer | None = None,
               gate: PromotionGate | None = None,
               constitution: Constitution | None = None,
               episode_store: ExperienceStore | None = None) -> InstrumentedApp:
    """Wrap ``app`` with graxella's observation + governance layer.

    All five auxiliary objects (``memory`` and ``society`` are required;
    ``tracer``, ``gate``, ``constitution``, ``episode_store`` are
    optional with sane defaults) are composed into one InstrumentedApp.
    Tracer hooks are wired so every write through memory + every routing
    decision through society flow into the tracer automatically.
    """
    tracer = tracer or UnifiedTracer.default()
    gate = gate or PromotionGate()
    constitution = constitution or Constitution.empty()
    episode_store = episode_store or InMemoryExperienceStore()

    # Wire tracer hooks — one unified event stream.
    memory.attach_tracer(tracer.hook_for("beliefs"))
    society.attach_tracer(tracer.hook_for("society"))
    gate.on_change(lambda p: tracer.record(
        "gate", f"proposal.{p.status.value}", {
            "id": p.id, "kind": p.kind, "score": p.score,
            "blast_radius": p.blast_radius, "payload": p.payload,
            "decided_by": p.decided_by, "note": p.note,
        },
    ))

    return InstrumentedApp(
        app=app,
        memory=memory,
        society=society,
        tracer=tracer,
        gate=gate,
        constitution=constitution,
        episode_store=episode_store,
    )


def _accepts_config(fn: Any) -> bool:
    """True if ``fn(x, config)`` is a supported signature."""
    if fn is None:
        return False
    try:
        import inspect
        sig = inspect.signature(fn)
        return "config" in sig.parameters or len(sig.parameters) >= 2
    except Exception:
        return False
