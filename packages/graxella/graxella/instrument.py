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

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from graxella.beliefs import Memory
from graxella.beliefs.records import render_recall_block
from graxella.constitution import Constitution
from graxella.gate import EvidenceGate
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
    gate: EvidenceGate
    constitution: Constitution
    episode_store: ExperienceStore
    domain: str | None = None      # evidence scope; defaults to memory namespace
    model_id: str | None = None    # which LLM serves dispatches (I4 scoping)
    recall: bool = True            # inject similar past cases at dispatch (0B)
    recall_top_k: int = 3
    # One id per instrumented app run — the Evidence Gate's provenance-
    # diversity defense counts distinct sessions, not distinct rows.
    session_id: str = field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:12]}")
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
        """Route a task through the mesh, auto-recording BOTH the decision
        and its typed outcome (task 0A-2). Not opt-in: a dispatch that
        produced no outcome is itself an error event — the Evidence Loop
        has no unverified path through this method.

        Also runs the Constitution's invariant checks on the RouteResult;
        violations surface as detection-only tracer events AND are counted
        on the outcome record.
        """
        domain = self.domain or self.memory.namespace
        t0 = time.perf_counter()

        # Case recall (0B): fetch verified similar experience BEFORE the
        # dispatch and hand it to Society as dispatch-time context. Recall
        # failures degrade loudly to no-recall — they never block routing.
        recall_block = ""
        if self.recall and "recall_context" not in kwargs:
            try:
                cases = self.memory.similar_cases(task, top_k=self.recall_top_k,
                                                  domain=domain)
                recall_block = render_recall_block(cases)
                if recall_block:
                    self.tracer.record("orchestrator", "recall.injected", {
                        "task": task[:200], "cases": len(cases),
                        "chars": len(recall_block),
                        "similarities": [c.similarity for c in cases],
                    })
            except Exception as exc:
                self.tracer.record("orchestrator", "degradation.recall", {
                    "err_class": type(exc).__name__, "err": str(exc)[:300],
                })
            kwargs["recall_context"] = recall_block or None

        try:
            result = self.society.route(task, **kwargs)
        except Exception as exc:
            # The dispatch itself blew up. Record the decision attempt and
            # a high-confidence failure outcome, then re-raise — loudly.
            aid = self.memory.record_decision(
                decision_type="delegate", task=task,
                chosen="<dispatch-error>",
                rationale=f"dispatch raised {type(exc).__name__}",
                confidence=0.0, domain=domain, model_id=self.model_id,
            )
            self.memory.record_outcome(
                decision_id=aid, ok=False,
                err=str(exc), err_class=type(exc).__name__,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                domain=domain, kind="delegate", model_id=self.model_id,
                session_id=self.session_id,
            )
            self.tracer.record("orchestrator", "dispatch.error", {
                "decision_id": aid, "task": task[:300],
                "err_class": type(exc).__name__, "err": str(exc)[:300],
            })
            raise

        chosen = f"{result.chosen_agent}::{result.chosen_skill}"
        aid = self.memory.record_decision(
            decision_type="delegate",
            task=task,
            chosen=chosen,
            rationale=result.rationale,
            confidence=result.score,
            domain=domain,
            model_id=self.model_id,
        )
        violations = self._enforce_constitution(
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
        ok, err = _infer_ok(result)
        tools_used = list(getattr(result, "tools_used", None) or [])
        self.memory.record_outcome(
            decision_id=aid,
            ok=ok,
            err=err,
            err_class="dispatch" if err else None,
            latency_ms=result.latency_ms if result.latency_ms is not None
                       else (time.perf_counter() - t0) * 1000.0,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            domain=domain,
            kind="delegate",
            chosen=chosen,
            violations=len(violations),
            model_id=self.model_id,
            session_id=self.session_id,
            tools_used=tools_used,
        )

        # Reasoning–action mismatch detector (task 1-7, MAST FM-2.6).
        # Detection-only: the agent CLAIMED an action its tool trail
        # doesn't show. Flag it loudly; never correct it silently.
        claim = _claimed_action(result.response or "")
        if claim and not tools_used:
            sig = self.memory.record_signal(
                kind="reasoning_action_mismatch",
                decision_id=aid,
                detail={"domain": domain, "agent": result.chosen_agent,
                        "claim": claim,
                        "response_head": (result.response or "")[:200]},
            )
            self.tracer.record(
                "constitution", "governance.reasoning_action_mismatch",
                {"decision_id": aid, "signal_id": sig,
                 "agent": result.chosen_agent, "claim": claim},
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
                              decision_id: str | None) -> list:
        violations = list(self.constitution.check_invariants(
            output, applies_to=applies_to))
        for v in violations:
            self.tracer.record(
                "constitution", "governance.constitution_violation",
                {**v.to_dict(), "decision_id": decision_id},
            )
        return violations

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
               gate: EvidenceGate | None = None,
               constitution: Constitution | None = None,
               episode_store: ExperienceStore | None = None,
               domain: str | None = None,
               model_id: str | None = None,
               recall: bool = True,
               recall_top_k: int = 3) -> InstrumentedApp:
    """Wrap ``app`` with graxella's observation + governance layer.

    All five auxiliary objects (``memory`` and ``society`` are required;
    ``tracer``, ``gate``, ``constitution``, ``episode_store`` are
    optional with sane defaults) are composed into one InstrumentedApp.
    Tracer hooks are wired so every write through memory + every routing
    decision through society flow into the tracer automatically.
    """
    tracer = tracer or UnifiedTracer.default()
    constitution = constitution or Constitution.empty()
    # The Evidence Gate reads the same ledger the app writes outcomes to —
    # one substrate; constitution invariants sit above it as hard blocks.
    gate = gate or EvidenceGate(memory, constitution=constitution)
    episode_store = episode_store or InMemoryExperienceStore()

    # Wire tracer hooks — one unified event stream.
    memory.attach_tracer(tracer.hook_for("beliefs"))
    society.attach_tracer(tracer.hook_for("society"))
    gate.attach_tracer(tracer.hook_for("gate"))

    return InstrumentedApp(
        app=app,
        memory=memory,
        society=society,
        tracer=tracer,
        gate=gate,
        constitution=constitution,
        episode_store=episode_store,
        domain=domain,
        model_id=model_id,
        recall=recall,
        recall_top_k=recall_top_k,
    )


#: Verbs whose first-person past tense claims a concrete action the tool
#: trail should corroborate. Deliberately conservative — a miss here is a
#: quiet non-event; a false positive erodes trust in the detector.
_CLAIM_RE = re.compile(
    r"\bI (?:have )?(checked|looked up|called|queried|searched|verified|"
    r"retrieved|fetched|ran|executed|updated|contacted|cancell?ed|issued)\b",
    re.IGNORECASE,
)


def _claimed_action(response: str) -> str | None:
    """The claimed action verb, when the response asserts one (1-7)."""
    m = _CLAIM_RE.search(response)
    return m.group(1).lower() if m else None


def _infer_ok(result: Any) -> tuple[bool, str | None]:
    """Judge a dispatch from its RouteResult (task 0A-2).

    Not-ok when: nothing was chosen (unroutable), the mesh surfaced a
    dispatch error in the response ("[error] ..."), or the explanation
    carries a blocking flag. The err string is the evidence, truncated.
    """
    flags = set(result.flags or ())
    if result.chosen_agent is None:
        return False, f"unroutable: {sorted(flags) or 'no candidate'}"
    response = result.response or ""
    if response.startswith("[error]"):
        return False, response[:500]
    if "conformance_blocked" in flags or "unroutable" in flags:
        return False, f"blocked: {sorted(flags)}"
    return True, None


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
