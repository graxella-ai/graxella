"""Mesh: the user-facing facade.

A Mesh ties together:
    capability graph  <-  Agent Cards (deterministic parse)
    router            <-  cheap embedding lookup
    conformance       <-  graph-derived guardrail
    dispatcher        <-  A2A JSON-RPC
    telemetry         <-  per-task record + report
    explanations      <-  human-readable per-decision rationale
    governance        <-  detection hooks (no auto-correct)
    metrics           <-  Prometheus-format counters + histograms
    store             <-  pluggable explanation persistence

This is the only class most users need.

Thread-safety model
-------------------
The `(graph, router)` pair is stored in a `_RoutingState` namedtuple.
`Society.run()` captures a reference to the current `_state` at the top
of each call (one GIL-atomic attribute read). The rest of the call uses
those captured local references, so a concurrent `apply_optimization()`
swap never races with an in-flight routing decision.

`apply_optimization()` does copy-on-write:

  1. deepcopy the live graph
  2. apply accepted edits on the copy
  3. build a new Router against the copy
  4. acquire `_swap_lock` (brief -- just two pointer writes) and swap
     `_state`

Routes running concurrently with step 1-3 continue against the old
(consistent) state. Routes started after step 4 use the new state.

The optimizer's backtest (`Society.optimize()`) also never touches the
live graph -- it works entirely against snapshot copies built in
`optimizer.py`.
"""
from __future__ import annotations

import threading
from collections import namedtuple
from copy import deepcopy
from dataclasses import replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# Score gap below which two routing candidates are considered nearly tied.
# Surfaced as the "VECTOR_AMBIGUITY" flag when top-3 are within this band.
_AMBIGUITY_BAND: float = 0.05

# Upper bound on remembered governance signatures. Beyond this we drop the
# oldest signatures so the in-memory set cannot grow without limit on a
# long-running process. The window is generous because conflict signatures
# are short strings; the cost is millions of bytes only at extreme scale.
_GOVERNANCE_SIG_CAP: int = 10_000

from ._logging import get_logger
from .adapters import adapt
from .card import AgentCard, load_card
from .conformance import check as conformance_check
from .dispatcher import (
    CompositeTransport,
    HttpTransport,
    LocalTransport,
    Transport,
    build_message_send_payload,
    extract_text,
)
from .embeddings import EmbedFn
from .exceptions import ConformanceViolation, DispatchError, NoRouteError
from .explanation import RoutingExplanation, build_rationale
from .governance import (
    CapabilityDrift,
    CapabilityDriftDetector,
    Conflict,
    ConflictDetector,
    GovernanceHooks,
    LowMarginHook,
)
from .graph import CapabilityGraph
from .handoff import Handoff
from .metrics import MetricsCollector
from .optimizer import (
    LLMSuggestFn,
    OptimizationReport,
    apply_optimization as _apply_optimization,
    optimize as _optimize,
)
from .router import RouteCandidate, Router
from .store import ExplanationStore, InMemoryStore
from .telemetry import RoutingRecord, TelemetrySink, estimate_tokens

_log = get_logger(__name__)


CardSource = Union[str, dict, AgentCard, Any]

# Immutable snapshot of the two objects that routing reads.
# Swapping _state is one attribute write -- GIL-atomic in CPython.
_RoutingState = namedtuple("_RoutingState", ["graph", "router"])


class Mesh:
    """A mesh of A2A agents routed by a deterministic capability graph.

    Every call to `run()` produces:
      * a RoutingRecord on the telemetry sink (token counts, fallbacks)
      * a RoutingExplanation indexed by handoff id (the human view)

    Governance hooks (`on_conflict`, `on_low_confidence`, `on_human_review`,
    `on_capability_drift`) are pure side effects -- they cannot block,
    re-route, or modify a dispatch. The package never silently auto-
    corrects; everything is detection and disclosure.
    """

    def __init__(
        self,
        *,
        embed_fn: Optional[EmbedFn] = None,
        transport: Optional[Transport] = None,
        min_score: float = 0.05,
        strict: bool = True,
        conflict_window: int = 50,
        store: Optional[ExplanationStore] = None,
        metrics: Optional[MetricsCollector] = None,
    ) -> None:
        self._embed_fn = embed_fn

        # Routing state -- accessed via properties; swapped atomically.
        _graph = CapabilityGraph()
        _router = Router(_graph, embed_fn=embed_fn)
        self._state = _RoutingState(_graph, _router)

        # Brief lock protecting the _state pointer swap.
        # Never held during routing computation -- only during the two-pointer
        # assignment in apply_optimization() / add() and the two-pointer read
        # at the top of run().
        self._swap_lock = threading.Lock()

        # Governance state lock (for _fired_conflicts / _fired_drifts sets).
        self._gov_lock = threading.Lock()

        self.telemetry = TelemetrySink()
        self.metrics = metrics if metrics is not None else MetricsCollector()
        self._local = LocalTransport()
        self._http = transport or HttpTransport()
        self._transport = CompositeTransport(self._local, self._http)
        self.min_score = min_score
        self.strict = strict
        self._store: ExplanationStore = store if store is not None else InMemoryStore()
        self._hooks = GovernanceHooks()
        self._conflict_detector = ConflictDetector(window=conflict_window)
        self._drift_detector = CapabilityDriftDetector()
        # Track "already-fired" governance signatures with insertion order so
        # the oldest can be evicted when the cap is exceeded. dict preserves
        # insertion order in Python 3.7+, giving us a poor-man's OrderedSet.
        self._fired_conflicts: Dict[str, None] = {}
        self._fired_drifts: Dict[str, None] = {}
        self._governance_sig_cap: int = _GOVERNANCE_SIG_CAP

    # ---- routing state (CoW properties) ----------------------------------
    @property
    def graph(self) -> CapabilityGraph:
        """The current capability graph. CoW -- reads are always consistent."""
        return self._state.graph

    @property
    def router(self) -> Router:
        """The current router. CoW -- always paired with the current graph."""
        return self._state.router

    # ---- mesh construction -----------------------------------------------
    def add(self, source: CardSource) -> AgentCard:
        """Add an agent to the mesh.

        Accepts:
          * a URL to an A2A agent card
          * a path to an agent-card.json file
          * a parsed AgentCard or dict in that shape
          * a native agent / crew / graph (auto-adapted)

        Uses CoW: builds the new (graph, router) pair and swaps atomically.
        """
        if isinstance(source, (str, dict, AgentCard)):
            card = load_card(source)
        else:
            adapted = adapt(source)
            if adapted is None:
                raise TypeError(
                    f"No adapter matched object of type {type(source).__name__}. "
                    "Register one with agent2society.adapters.register_adapter."
                )
            card = adapted.card
            self._local.register(card.url, adapted.handler)

        # CoW: snapshot current graph, add to copy, build new router, swap.
        with self._swap_lock:
            new_graph = deepcopy(self._state.graph)
            new_graph.add_agent(card)
            new_router = Router(new_graph, embed_fn=self._embed_fn)
            self._state = _RoutingState(new_graph, new_router)
        return card

    def boundary(
        self,
        agent: str,
        *,
        allow: Optional[Iterable[str]] = None,
        deny: Optional[Iterable[str]] = None,
    ) -> "Mesh":
        """Tighten an agent's declared boundary beyond what its card says.

        Copy-on-write: mutates a deepcopy of the live graph and swaps the
        whole `(graph, router)` pair atomically. In-flight routes continue
        against the snapshot they captured at entry.
        """
        # Step 1: snapshot the current state (one pointer read under lock).
        with self._swap_lock:
            current_state = self._state
        # Step 2-3: heavy work outside the lock.
        new_graph = deepcopy(current_state.graph)
        new_graph.set_boundary(agent, allow=allow, deny=deny)
        new_router = Router(new_graph, embed_fn=self._embed_fn)
        # Step 4: atomic swap.
        with self._swap_lock:
            self._state = _RoutingState(new_graph, new_router)
        return self

    def depends_on(self, agent: str, *deps: str) -> "Mesh":
        """Add declared dependencies between agents via copy-on-write."""
        with self._swap_lock:
            current_state = self._state
        new_graph = deepcopy(current_state.graph)
        for d in deps:
            new_graph.add_dependency(agent, d)
        new_router = Router(new_graph, embed_fn=self._embed_fn)
        with self._swap_lock:
            self._state = _RoutingState(new_graph, new_router)
        return self

    # ---- governance hook registration -----------------------------------
    def on_conflict(self, handler: Callable[[Conflict], None]) -> "Mesh":
        """Register a handler fired when the same task is routed differently
        across handoffs in the window."""
        self._hooks.on_conflict.append(handler)
        return self

    def on_low_confidence(
        self,
        handler: Callable[[RoutingExplanation], None],
        *,
        threshold: Optional[float] = None,
    ) -> "Mesh":
        self._hooks.on_low_confidence.append(handler)
        if threshold is not None:
            self._hooks.low_confidence_threshold = float(threshold)
        return self

    def on_capability_drift(
        self, handler: Callable[[CapabilityDrift], None]
    ) -> "Mesh":
        self._hooks.on_capability_drift.append(handler)
        return self

    def on_human_review(
        self, handler: Callable[[RoutingExplanation, str], None]
    ) -> "Mesh":
        self._hooks.on_human_review.append(handler)
        return self

    def on_low_margin(
        self,
        handler: LowMarginHook,
        *,
        threshold: float = 0.05,
    ) -> "Mesh":
        """Register a handler fired when the score gap between the chosen
        candidate and the runner-up is below `threshold`.

        A narrow margin means two agents nearly tied -- the decision is
        fragile. The explanation's `margin` field carries the exact gap;
        the explanation's `flags` tuple will include "LOW_MARGIN".

        Default threshold: 0.05 (5 percentage points on a 0-1 score scale).
        """
        self._hooks.on_low_margin.append(handler)
        self._hooks.low_margin_threshold = float(threshold)
        return self

    # ---- routing & dispatch ---------------------------------------------
    def route(
        self,
        task: str,
        *,
        tags: Optional[Sequence[str]] = None,
        top_k: int = 5,
    ) -> List[RouteCandidate]:
        """Return ranked (agent, skill) candidates without dispatching."""
        return self._state.router.route(task, tags=tags, top_k=top_k)

    def run(
        self,
        task: Union[str, Handoff],
        *,
        tags: Optional[Sequence[str]] = None,
        top_k: int = 5,
        retry: bool = False,
    ) -> str:
        """Resolve, guard, dispatch -- return the agent's text response.

        Accepts a bare string (backwards-compatible) or a Handoff envelope.

        `retry=True` falls through to the next conformance-passing candidate
        on a DispatchError, recording each attempt in the routing record.

        Concurrency: captures a consistent (graph, router) snapshot at entry.
        A concurrent `apply_optimization()` swap cannot race with an in-flight
        route -- the captured `state` references are stable for the lifetime
        of this call.
        """
        handoff = task if isinstance(task, Handoff) else Handoff.from_string(task)

        # Atomic snapshot: one GIL-guaranteed read. Never held during routing.
        with self._swap_lock:
            state = self._state

        record = RoutingRecord(
            task=handoff.task,
            chosen_agent=None,
            chosen_skill=None,
            score=None,
            handoff_id=handoff.id,
            intent=handoff.intent,
        )
        record.request_tokens = estimate_tokens(handoff.task)

        candidates = state.router.route(handoff.task, tags=tags, top_k=top_k)
        record.candidates = [c.to_dict() for c in candidates]

        chosen, decorated_candidates, blocked_reason = self._select_with_conformance(
            handoff, candidates, graph=state.graph
        )

        margin, flags = _compute_routing_signals(
            candidates=candidates,
            chosen=chosen,
            min_score=self.min_score,
            low_margin_threshold=self._hooks.low_margin_threshold,
        )

        explanation = self._build_explanation(
            handoff=handoff,
            chosen=chosen,
            alternatives=decorated_candidates,
            blocked_reason=blocked_reason,
            graph=state.graph,
            margin=margin,
            flags=flags,
        )
        record.explanation_id = handoff.id
        self._store_explanation(handoff.id, explanation)

        # Routing-quality signals are pre-dispatch: fire regardless of whether
        # the transport call succeeds. LOW_MARGIN / VECTOR_AMBIGUITY / OOD are
        # properties of the routing decision, not the agent's response.
        self._fire_routing_signal_hooks(explanation)

        if chosen is None:
            return self._handle_unroutable(
                handoff=handoff,
                record=record,
                candidates=candidates,
                blocked_reason=blocked_reason,
            )

        record.chosen_agent = chosen.agent
        record.chosen_skill = chosen.skill_id
        record.score = chosen.score
        record.fallbacks = [
            {
                "agent": c.agent,
                "skill": c.skill_id,
                "reason": c.rejected_reason,
            }
            for c in decorated_candidates
            if c.rejected_reason
            and c.rejected_reason != "below min_score"
            and c is not chosen
        ]

        dispatch_sequence: List[RouteCandidate] = [chosen]
        if retry:
            for c in decorated_candidates:
                if c is chosen or c.rejected_reason:
                    continue
                dispatch_sequence.append(c)

        last_error: Optional[DispatchError] = None
        text: Optional[str] = None
        dispatched_cand: Optional[RouteCandidate] = None
        for attempt_idx, cand in enumerate(dispatch_sequence):
            node = state.graph.require(cand.agent)
            payload = build_message_send_payload(
                task=handoff.task, skill_id=cand.skill_id
            )
            try:
                response = self._transport.send(node.url, payload)
                text = extract_text(response)
                dispatched_cand = cand
                if attempt_idx > 0:
                    record.fallbacks.append(
                        {
                            "agent": cand.agent,
                            "skill": cand.skill_id,
                            "reason": f"retry after {last_error}",
                        }
                    )
                    self.metrics.inc(
                        "agent2society_dispatch_retries_total",
                        labels={"agent": cand.agent, "skill": cand.skill_id},
                    )
                break
            except DispatchError as e:
                last_error = e
                is_last = attempt_idx == len(dispatch_sequence) - 1
                if not retry or is_last:
                    record.error = str(e)
                    self.telemetry.add(record)
                    self.metrics.inc(
                        "agent2society_dispatch_failures_total",
                        labels={"agent": cand.agent, "skill": cand.skill_id},
                    )
                    self.metrics.inc(
                        "agent2society_routes_total",
                        labels={"outcome": "dispatch_failed"},
                    )
                    if self.strict:
                        raise
                    return ""
                # Retry path: record this attempt as a fallback reason so the
                # telemetry / explanation surface still shows what we tried.
                record.fallbacks.append(
                    {
                        "agent": cand.agent,
                        "skill": cand.skill_id,
                        "reason": f"dispatch failed: {e}",
                    }
                )
                self.metrics.inc(
                    "agent2society_dispatch_failures_total",
                    labels={"agent": cand.agent, "skill": cand.skill_id},
                )
                continue

        # Defensive: the loop above is guaranteed to either set text and
        # dispatched_cand, or raise / return. If both are still None we hit
        # an impossible branch -- surface it loudly rather than via assert.
        if text is None or dispatched_cand is None:
            err = DispatchError(
                "dispatch loop exited without a successful response "
                "(internal invariant violated)"
            )
            record.error = str(err)
            self.telemetry.add(record)
            self.metrics.inc(
                "agent2society_routes_total",
                labels={"outcome": "dispatch_failed"},
            )
            if self.strict:
                raise err
            return ""
        record.dispatched = True
        record.response_text = text
        record.response_tokens = estimate_tokens(text)
        self.telemetry.add(record)

        self.metrics.inc(
            "agent2society_routes_total",
            labels={
                "agent": dispatched_cand.agent,
                "skill": dispatched_cand.skill_id,
                "outcome": "dispatched",
            },
        )
        self.metrics.inc(
            "agent2society_dispatches_total",
            labels={"agent": dispatched_cand.agent, "skill": dispatched_cand.skill_id},
        )
        self.metrics.observe("agent2society_route_score", chosen.score)
        self.metrics.observe("agent2society_request_tokens", record.request_tokens)
        self.metrics.observe("agent2society_response_tokens", record.response_tokens)

        self._fire_post_dispatch_hooks(handoff, explanation, text)
        return text

    # ---- explanation access ---------------------------------------------
    def explain(self, handoff_id: str) -> Optional[RoutingExplanation]:
        return self._store.get(handoff_id)

    def explanations(self) -> List[RoutingExplanation]:
        return list(self._store.all())

    def last_explanation(self) -> Optional[RoutingExplanation]:
        ids = self._store.ids()
        if not ids:
            return None
        return self._store.get(ids[-1])

    @property
    def store(self) -> ExplanationStore:
        return self._store

    # ---- optimization (v0.4 + v0.5) -------------------------------------
    def optimize(
        self,
        labels: Sequence[Tuple[str, str, str]],
        *,
        max_tags_per_skill: int = 3,
        llm_fn: Optional[LLMSuggestFn] = None,
    ) -> OptimizationReport:
        """Mine discriminative tags from labeled past decisions.

        The live graph is never mutated during this call. All backtest
        routing runs against deep-copied snapshots in optimizer.py.
        Returns an OptimizationReport; nothing changes until you call
        apply_optimization(report).
        """
        store_mapping = (
            self._store.as_mapping()
            if hasattr(self._store, "as_mapping")
            else {hid: self._store.get(hid) for hid in self._store.ids()}
        )
        # Snapshot (graph, embed_fn) for the optimizer -- safe to read
        # outside the swap lock because optimize() never writes.
        with self._swap_lock:
            state = self._state
        return _optimize(
            graph=state.graph,
            embed_fn=self._embed_fn,
            explanations=store_mapping,
            labels=labels,
            max_tags_per_skill=max_tags_per_skill,
            llm_fn=llm_fn,
        )

    def apply_optimization(self, report: OptimizationReport) -> int:
        """Apply accepted edits via copy-on-write.

        1. deepcopy the current graph (outside the swap lock -- may take ms)
        2. apply accepted edits on the copy
        3. build a new Router against the copy
        4. brief swap_lock to atomically replace _state

        Routes running concurrently with steps 1-3 continue against the old
        (consistent) state and complete normally.
        """
        # Step 1-3: expensive work outside the lock.
        with self._swap_lock:
            current_state = self._state
        new_graph = deepcopy(current_state.graph)
        new_router = Router(new_graph, embed_fn=self._embed_fn)
        applied = _apply_optimization(new_graph, new_router, report)

        # Step 4: atomic swap (two pointer writes).
        with self._swap_lock:
            self._state = _RoutingState(new_graph, new_router)

        self.metrics.inc("agent2society_optimizer_edits_applied_total", by=applied)
        return applied

    # ---- reporting -------------------------------------------------------
    def report(self, *, render: bool = True) -> str:
        out = self.telemetry.render()
        if render:
            print(out)
        return out

    # ---- introspection ---------------------------------------------------
    def agents(self) -> List[str]:
        return [n.name for n in self._state.graph.agents()]

    def describe(self) -> List[dict]:
        result = []
        for n in self._state.graph.agents():
            sa = n.card.self_assessment
            result.append(
                {
                    "name": n.name,
                    "url": n.url,
                    "skills": [
                        {"id": s.id, "name": s.name, "tags": s.tags}
                        for s in n.card.skills
                    ],
                    "boundary": {
                        "allow": list(n.boundary.allow),
                        "deny": list(n.boundary.deny),
                    },
                    "depends_on": sorted(n.depends_on),
                    "self_assessment": sa.to_dict() if sa else None,
                }
            )
        return result

    # ---- internals -------------------------------------------------------
    def _select_with_conformance(
        self,
        handoff: Handoff,
        candidates: Sequence[RouteCandidate],
        *,
        graph: CapabilityGraph,
    ) -> Tuple[Optional[RouteCandidate], List[RouteCandidate], Optional[str]]:
        decorated: List[RouteCandidate] = []
        chosen: Optional[RouteCandidate] = None
        for cand in candidates:
            if cand.score < self.min_score:
                decorated.append(replace(cand, rejected_reason="below min_score"))
                continue
            res = conformance_check(
                graph,
                agent=cand.agent,
                skill_id=cand.skill_id,
                task=handoff.task,
            )
            if res.ok:
                if chosen is None:
                    chosen = cand
                decorated.append(cand)
            else:
                decorated.append(replace(cand, rejected_reason=res.reason))

        blocked_reason: Optional[str] = None
        if chosen is None and any(c.score >= self.min_score for c in candidates):
            blocked_reason = "all candidates above min_score failed conformance"
        return chosen, decorated, blocked_reason

    def _build_explanation(
        self,
        *,
        handoff: Handoff,
        chosen: Optional[RouteCandidate],
        alternatives: Sequence[RouteCandidate],
        blocked_reason: Optional[str],
        graph: CapabilityGraph,
        margin: float = 0.0,
        flags: Tuple[str, ...] = (),
    ) -> RoutingExplanation:
        features: Dict[str, float] = {
            "min_score": self.min_score,
            "confidence_required": handoff.confidence_required,
        }
        agent_caveats: List[str] = []
        if chosen is not None:
            features.update(
                {
                    "score": round(chosen.score, 4),
                    "semantic": round(chosen.semantic, 4),
                    "tag_overlap": round(chosen.tag_overlap, 4),
                }
            )
            node = graph.get(chosen.agent)
            if node is not None and node.card.self_assessment is not None:
                sa = node.card.self_assessment
                agent_caveats.extend(sa.known_limitations)
                if sa.escalate_when:
                    agent_caveats.extend(f"escalate when: {w}" for w in sa.escalate_when)
                if sa.out_of_scope:
                    agent_caveats.extend(f"out of scope: {w}" for w in sa.out_of_scope)
            if not agent_caveats:
                agent_caveats.append("no self-assessment declared by this agent")
        rationale = build_rationale(
            task=handoff.task,
            chosen=chosen,
            alternatives=alternatives,
            blocked_reason=blocked_reason,
        )
        return RoutingExplanation(
            handoff_id=handoff.id,
            task=handoff.task,
            intent=handoff.intent,
            chosen_agent=chosen.agent if chosen else None,
            chosen_skill=chosen.skill_id if chosen else None,
            rationale=rationale,
            features_fired=features,
            alternatives=list(alternatives),
            confidence=chosen.score if chosen else 0.0,
            agent_self_caveats=agent_caveats,
            blocked_reason=blocked_reason,
            prior_chain=[p.to_dict() for p in handoff.prior],
            assumptions=list(handoff.assumptions),
            margin=margin,
            flags=flags,
        )

    def _store_explanation(self, handoff_id: str, exp: RoutingExplanation) -> None:
        self._store.put(exp)

    def _handle_unroutable(
        self,
        *,
        handoff: Handoff,
        record: RoutingRecord,
        candidates: Sequence[RouteCandidate],
        blocked_reason: Optional[str],
    ) -> str:
        if blocked_reason is not None:
            top = candidates[0]
            violation = {
                "agent": top.agent,
                "skill": top.skill_id,
                "reason": blocked_reason,
            }
            record.violation = violation
            self.telemetry.add(record)
            self.metrics.inc(
                "agent2society_conformance_blocked_total",
                labels={"agent": top.agent, "skill": top.skill_id},
            )
            self.metrics.inc(
                "agent2society_routes_total", labels={"outcome": "blocked"}
            )
            self._maybe_fire_governance()
            if self.strict:
                raise ConformanceViolation(
                    agent=top.agent,
                    skill=top.skill_id,
                    reason=blocked_reason,
                    task=handoff.task,
                )
            return ""
        self.telemetry.add(record)
        self.metrics.inc("agent2society_unroutable_total")
        self.metrics.inc(
            "agent2society_routes_total", labels={"outcome": "unroutable"}
        )
        if self.strict:
            raise NoRouteError(
                task=handoff.task,
                candidates=[c.to_dict() for c in candidates],
            )
        return ""

    def _fire_post_dispatch_hooks(
        self,
        handoff: Handoff,
        explanation: RoutingExplanation,
        result_text: str,
    ) -> None:
        threshold = handoff.confidence_required
        if threshold <= 0 and self._hooks.low_confidence_threshold is not None:
            threshold = self._hooks.low_confidence_threshold
        if threshold > 0 and explanation.confidence < threshold:
            self.metrics.inc("agent2society_low_confidence_total")
            for h in self._hooks.on_low_confidence:
                _safe_call(h, explanation, where="on_low_confidence")

        if handoff.human_review_when is not None:
            try:
                needs = bool(handoff.human_review_when(result_text))
            except Exception as e:
                # Fail safe: if the predicate raises, escalate to human review.
                # Silently swallowing here could let a borderline response
                # through that a working predicate would have flagged.
                _log.warning(
                    "human_review_when predicate raised %s; defaulting to "
                    "needs-review=True for handoff %s",
                    e.__class__.__name__,
                    handoff.id,
                )
                self.metrics.inc("agent2society_human_review_predicate_errors_total")
                needs = True
            if needs:
                self.metrics.inc("agent2society_human_review_total")
                for h in self._hooks.on_human_review:
                    _safe_call(h, explanation, result_text, where="on_human_review")

        self._maybe_fire_governance()

    def _fire_routing_signal_hooks(self, explanation: RoutingExplanation) -> None:
        """Fire pre-dispatch routing-quality signals.

        Called before the transport attempt so OOD / LOW_MARGIN / VECTOR_AMBIGUITY
        are always recorded, even when dispatch subsequently fails.
        """
        if "LOW_MARGIN" in explanation.flags:
            self.metrics.inc("agent2society_low_margin_total")
            for h in self._hooks.on_low_margin:
                _safe_call(h, explanation, where="on_low_margin")
        if "VECTOR_AMBIGUITY" in explanation.flags:
            self.metrics.inc("agent2society_vector_ambiguity_total")
        if "OOD" in explanation.flags:
            self.metrics.inc("agent2society_ood_total")

    def _maybe_fire_governance(self) -> None:
        # `store.all()` may already return a snapshot list -- only convert if
        # we got back a non-list Sequence (e.g. a tuple). Avoids one big copy
        # on every dispatch for stores that already snapshot.
        snapshot = self._store.all()
        all_exps = snapshot if isinstance(snapshot, list) else list(snapshot)
        for c in self._conflict_detector.detect(all_exps):
            sig = c.kind + "|" + ",".join(sorted(c.handoff_ids))
            if not self._remember_signature(self._fired_conflicts, sig):
                continue
            self.metrics.inc(
                "agent2society_conflicts_detected_total", labels={"kind": c.kind}
            )
            for h in self._hooks.on_conflict:
                _safe_call(h, c, where="on_conflict")
        for d in self._drift_detector.detect(all_exps):
            sig = d.agent + "|" + ",".join(sorted(d.skills_seen))
            if not self._remember_signature(self._fired_drifts, sig):
                continue
            self.metrics.inc(
                "agent2society_drift_detected_total", labels={"agent": d.agent}
            )
            for h in self._hooks.on_capability_drift:
                _safe_call(h, d, where="on_capability_drift")

    def _remember_signature(self, seen: Dict[str, None], sig: str) -> bool:
        """Insert sig into the sliding window; return True if it was new.

        When the window is full, we evict the oldest signature (FIFO via
        dict insertion order). The cap is large enough that real systems
        never thrash; small enough that an unbounded loop cannot OOM us.
        """
        with self._gov_lock:
            if sig in seen:
                return False
            seen[sig] = None
            # Evict oldest entries past the cap. dict iteration order is
            # insertion order in CPython 3.7+, so the first key is oldest.
            while len(seen) > self._governance_sig_cap:
                oldest = next(iter(seen))
                del seen[oldest]
            return True


def _safe_call(handler: Callable, *args, where: str = "hook") -> None:
    """Invoke a user-registered hook. Swallows exceptions so a buggy hook
    can never block a dispatch, but logs them so they are not silent."""
    try:
        handler(*args)
    except Exception as e:  # pragma: no cover - exercised by resilience tests
        _log.warning(
            "%s handler %r raised %s: %s",
            where,
            getattr(handler, "__qualname__", repr(handler)),
            e.__class__.__name__,
            e,
        )


def _compute_routing_signals(
    *,
    candidates: Sequence["RouteCandidate"],
    chosen: Optional["RouteCandidate"],
    min_score: float,
    low_margin_threshold: Optional[float],
) -> Tuple[float, Tuple[str, ...]]:
    """Derive margin and structured flags from the already-sorted candidates.

    All arithmetic is O(1) against a list that was already computed by the
    router -- zero additional cost in the hot path.

    Returns (margin, flags) where flags is a subset of:
      "OOD"              -- no candidate scored above min_score
      "VECTOR_AMBIGUITY" -- top-3 above-threshold candidates within
                            _AMBIGUITY_BAND of each other
      "LOW_MARGIN"       -- score gap between top-1 and top-2 is below
                            the registered low_margin_threshold
    """
    flags: List[str] = []

    # margin = top-1 minus top-2 score (0 when fewer than 2 candidates).
    if len(candidates) >= 2:
        margin = candidates[0].score - candidates[1].score
    elif candidates:
        margin = candidates[0].score
    else:
        margin = 0.0

    # OOD: no candidate at all, or every candidate below the threshold.
    if not candidates or candidates[0].score < min_score:
        flags.append("OOD")
    else:
        # VECTOR_AMBIGUITY: top-3 above-threshold candidates within the band.
        above = [c for c in candidates if c.score >= min_score]
        if len(above) >= 3 and (above[0].score - above[2].score) < _AMBIGUITY_BAND:
            flags.append("VECTOR_AMBIGUITY")

        # LOW_MARGIN: gap between winner and runner-up below the threshold.
        if (
            chosen is not None
            and low_margin_threshold is not None
            and margin < low_margin_threshold
        ):
            flags.append("LOW_MARGIN")

    return margin, tuple(flags)
