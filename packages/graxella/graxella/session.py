"""graxella.session — the one-object developer surface (Tier 1).

A ``Session`` owns the governance tuple that every other constructor
wants threaded in by hand — ``(memory, rulebook, gate, domain,
model_id)`` — and hangs the developer-facing verbs off it:

    import graxella

    grx = graxella.Session("refund-desk", domain="refunds",
                           model_id="qwen2.5:3b")

    @grx.tool                              # observed: every call -> ledger
    def check_order(order_id: str) -> str:
        \"\"\"look up an order\"\"\"
        ...

    @grx.healer                            # session-wide heal policy
    def llm_healer(tool_name, args, error):
        ...
        return graxella.TransformRecipe(field_map={old: "order_ref"})

    @grx.tool(fallback=shipping_v2)        # healed: the full heal ladder
    def get_shipping_status(order_id: str) -> str:
        \"\"\"get the live shipping status\"\"\"
        ...

    app = grx.mesh([triage, responder])    # tuple threaded automatically
    t = app.run_trajectory(task, max_hops=3)

    for p in grx.pending():                # governance verbs, never hidden
        grx.approve(p, by="operator:me", note="verified")
    grx.save_topology()

Design rules:

  * ``@grx.tool`` returns a real LangChain ``BaseTool`` — it drops into
    ``create_agent(llm, [...])`` unchanged. Graxella stays invisible at
    the framework boundary.
  * No module-level implicit session: the ``grx`` instance is explicit,
    greppable, and two independent sessions coexist in one process.
  * The gate is never bypassed: ``approve`` records the human decision,
    re-decides through the Evidence Gate, and only then promotes.
  * Everything here composes the existing primitives (Tier 2:
    ``Memory``, ``Rulebook``, ``EvidenceGate``, ``ToolInterceptor``,
    ``mesh``), which all remain public and reachable as attributes.
"""
from __future__ import annotations

import functools
import inspect
import logging
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graxella.api.topology import render_html, topology_data
from graxella.beliefs import Memory
from graxella.gate.evidence import EvidenceGate, GateDecision, pending_from_ledger
from graxella.gate.spec import ProposalStatus
from graxella.healing.interceptor import Healer, ToolInterceptor
from graxella.mesh import mesh as _mesh
from graxella.mesh import supervisor as _supervisor
from graxella.rulebook import Rulebook

_log = logging.getLogger("graxella")

#: Default project-local root for session workdirs.
SESSIONS_ROOT = Path(".graxella")

#: "not yet attempted" marker for the lazily-built default healer.
_UNSET = object()


@dataclass
class ReconcileResult:
    """What one Session.reconcile() pass did, both directions."""
    promoted: list = field(default_factory=list)
    demoted: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.promoted or self.demoted)


class Session:
    """One governed run: workdir + memory + rulebook + gate, plus the
    decorators and verbs that use them. See the module docstring for
    the canonical example."""

    def __init__(self, name: str, *,
                 domain: str | None = None,
                 model_id: str | None = None,
                 workdir: str | Path | None = None,
                 memory: Memory | None = None,
                 namespace: str | None = None,
                 constitution: Any = None,
                 auto_heal: bool = True,
                 drift_classifier: Any = None) -> None:
        self.name = name
        self.domain = domain
        self.model_id = model_id
        self.constitution = constitution
        #: When True (default), a drifted @grx.tool(fallback=...) with no
        #: explicit healer heals through graxella's built-in reasoner (DSPy
        #: under the hood, or direct Ollama when DSPy isn't installed). The
        #: LLM still fires ONCE; its recipe is cached + gate-reviewed. An
        #: explicit @grx.healer always overrides this.
        self.auto_heal = auto_heal
        #: Optional override for what counts as drift (exc -> class|None).
        #: Default: healing.interceptor.classify_drift — signature drift,
        #: validation-library rejections, HTTP 410 / endpoint-404.
        self.drift_classifier = drift_classifier

        if workdir == "ephemeral":
            self.workdir = Path(tempfile.mkdtemp(prefix=f"{name}-"))
            _log.warning(
                "graxella: EPHEMERAL session at %s — evidence from this "
                "run is discarded and nothing will be learned from it",
                self.workdir)
        else:
            self.workdir = Path(workdir) if workdir else SESSIONS_ROOT / name
            self.workdir.mkdir(parents=True, exist_ok=True)

        # The governance tuple, built once (WAL-buffered by default —
        # ledger writes ride off the dispatch hot path).
        self.memory = memory or Memory.sqlite(
            str(self.workdir / "mnema.db"), agent_id=name,
            namespace=namespace or domain or "default", buffered=True)
        self.rulebook = Rulebook(path=self.workdir / "rulebook.json")
        self.gate = EvidenceGate(self.memory, constitution=constitution)

        self._healer: Healer | None = None
        #: Lazily-built built-in healer (DSPy / Ollama). Sentinel _UNSET
        #: means "not attempted yet"; None means "attempted, unavailable".
        self._default_healer: Any = _UNSET
        #: {tool_name: ToolInterceptor} — the governed core behind each
        #: decorated tool, for direct inspection (trust, healer_calls).
        self.interceptors: dict[str, ToolInterceptor] = {}
        #: {tool_name: BaseTool} — what ``@grx.tool`` returned.
        self.tools: dict[str, Any] = {}
        #: The most recent mesh/supervisor app (used by save_topology).
        self.app: Any = None

    # ---------------------------------------------------------- decorators

    def tool(self, fn: Callable | None = None, *,
             fallback: Callable[[dict], Any] | None = None,
             healer: Healer | None = None,
             name: str | None = None):
        """Decorator: turn a plain function into a governed LangChain tool.

        Bare form — observed only (ledger decision + outcome per call):

            @grx.tool
            def check_order(order_id: str) -> str:
                \"\"\"look up an order\"\"\"

        With ``fallback`` — the full heal ladder (happy path → promoted
        transform → heal-once → loud failure). No healer is required: when
        the tool drifts and none is configured, graxella's built-in healer
        proposes the repair recipe once (DSPy under the hood, or direct
        Ollama when DSPy isn't installed), applies it deterministically, and
        ships it to the Evidence Gate for review. ``fallback`` takes the
        POST-transform args dict (the new API's schema), so its contract is
        ``dict -> Any``, not the primary's signature:

            @grx.tool(fallback=shipping_v2)      # heals automatically
            def get_shipping_status(order_id: str) -> str:
                \"\"\"get the live shipping status\"\"\"

        ``healer`` overrides both the session healer and the built-in
        default for this tool only (set ``auto_heal=False`` on the Session
        to disable the built-in entirely).
        Returns a ``StructuredTool`` (schema inferred from the function
        signature) that drops straight into ``create_agent``.
        """
        def deco(f: Callable):
            return self._build_tool(f, fallback=fallback,
                                    healer=healer, name=name)
        return deco(fn) if fn is not None else deco

    def healer(self, fn: Healer) -> Healer:
        """Decorator: register the session-wide healer — the ONE place an
        LLM may appear in healing. Every ``@grx.tool(fallback=...)``
        inherits it (late-bound: registration order doesn't matter)."""
        self._healer = fn
        return fn

    def _resolve_skill(self, name: str):
        """Resolve a promoted rule's ``with_skill`` to a callable over this
        session's registered tools — so a tool_binding rule dispatches to
        the tool it actually names. Returns None when unregistered (the
        interceptor then degrades loudly to its configured fallback)."""
        bt = self.tools.get(name)
        if bt is None:
            return None
        return lambda args, _t=bt: _t.invoke(dict(args))

    def _ensure_default_healer(self) -> Healer | None:
        """Lazily build graxella's built-in healer (DSPy under the hood, or
        direct Ollama when DSPy isn't installed). Built at most once, only
        when a drift with a fallback actually needs it -- sessions that never
        drift never spin up a model. Returns None when auto_heal is off or no
        engine is reachable, in which case the drift fails loudly."""
        if not self.auto_heal:
            return None
        if self._default_healer is _UNSET:
            from graxella.healing.dspy_healer import build_default_healer
            self._default_healer = build_default_healer(model_id=self.model_id)
        return self._default_healer

    @property
    def healer_calls(self) -> int:
        """Total healer (LLM) invocations across all tools, ever."""
        return sum(i.healer_calls for i in self.interceptors.values())

    def _build_tool(self, f: Callable, *,
                    fallback: Callable[[dict], Any] | None,
                    healer: Healer | None,
                    name: str | None):
        tool_name = name or f.__name__
        sig = inspect.signature(f)

        def _late_healer(t: str, args: dict, error: str):
            # Resolution order, all late-bound so registration order is free:
            #   1. per-tool healer=   (explicit, this tool only)
            #   2. session @grx.healer (explicit, all tools)
            #   3. built-in default   (DSPy/Ollama, when auto_heal is on)
            h = healer or self._healer or self._ensure_default_healer()
            return h(t, args, error) if h is not None else None

        interceptor = ToolInterceptor(
            lambda args: f(**args),
            tool_name=tool_name, memory=self.memory,
            rulebook=self.rulebook, gate=self.gate,
            fallback=fallback, healer=_late_healer,
            domain=self.domain or "default", model_id=self.model_id,
            skill_resolver=self._resolve_skill,
            drift_classifier=self.drift_classifier)

        @functools.wraps(f)
        def runner(*args: Any, **kwargs: Any) -> Any:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            return interceptor(dict(bound.arguments))

        # Deferred: langchain_core is the host framework's dep, not ours.
        from langchain_core.tools import StructuredTool
        bt = StructuredTool.from_function(
            runner, name=tool_name,
            description=(f.__doc__ or tool_name).strip())

        self.interceptors[tool_name] = interceptor
        self.tools[tool_name] = bt
        return bt

    # ---------------------------------------------------------- orchestration

    def mesh(self, agents: Iterable[Any], **kwargs: Any):
        """``graxella.mesh(...)`` with the session's tuple pre-threaded.
        Extra kwargs (router=, recall=, tracer=, ...) pass through."""
        kwargs.setdefault("store_path", str(self.workdir / "routes.jsonl"))
        kwargs.setdefault("domain", self.domain)
        kwargs.setdefault("model_id", self.model_id)
        kwargs.setdefault("constitution", self.constitution)
        self.app = _mesh(agents, memory=self.memory, gate=self.gate, **kwargs)
        return self.app

    def supervisor(self, agents: Iterable[Any], model: Any, **kwargs: Any):
        """``graxella.supervisor(...)`` with the session's tuple
        pre-threaded. ``model`` is the supervisor's own LLM."""
        kwargs.setdefault("store_path",
                          str(self.workdir / "supervisor-routes.jsonl"))
        kwargs.setdefault("domain", self.domain)
        kwargs.setdefault("model_id", self.model_id)
        kwargs.setdefault("constitution", self.constitution)
        self.app = _supervisor(agents, model, memory=self.memory,
                               gate=self.gate, **kwargs)
        return self.app

    # ---------------------------------------------------------- governance

    @staticmethod
    def _pid(proposal: Any) -> str:
        """Accept a proposal id, a Proposal, or a pending() row."""
        if isinstance(proposal, str):
            return proposal
        if isinstance(proposal, dict):
            return proposal["proposal_id"]
        return proposal.id

    def pending(self) -> list[dict]:
        """The cross-process human-review queue, straight from the
        ledger (proposal_id, kind, domain, reason, posterior, ...)."""
        return pending_from_ledger(self.memory)

    def approve(self, proposal: Any, *, by: str, note: str = "",
                promote: bool = True):
        """Record a human approval, fold it through the Evidence Gate,
        and (by default) promote the approved rule to the rulebook.

        Never bypasses the gate: hard blocks and auto-rejects stand even
        against a human approval. Returns the ApprovedRule when promoted,
        the decided Proposal when not, or None when the proposal object
        isn't held by this process (the decision is still on the ledger —
        the next decide() anywhere honors it).
        """
        pid = self._pid(proposal)
        live = self.gate.get(pid)          # grab BEFORE approve pops it
        self.gate.approve(pid, by=by, note=note)
        if live is None:
            return None
        self.gate.refresh()
        _, decided = self.gate.decide(live)
        if promote and decided.status in (ProposalStatus.APPROVED,
                                          ProposalStatus.ACTIVE):
            return self.rulebook.promote(
                decided, approved_by=by,
                domain=decided.target.domain or self.domain or "default")
        return decided

    def reject(self, proposal: Any, *, by: str, note: str = "") -> str:
        """Record a human rejection. Rejections are always honored."""
        return self.gate.reject(self._pid(proposal), by=by, note=note)

    def reconcile(self, *, by: str = "gate:evidence") -> "ReconcileResult":
        """Autonomous governance pass -- reconciles the rulebook with what
        the ledger currently says, in both directions:

          * PROMOTE (the operator-free twin of approve()): re-decides each
            healed tool's standing transform proposal against the
            accumulated ledger, and promotes the ones the Evidence Gate
            now AUTO-APPROVEs -- posterior clears the self-calibrating
            threshold AND successes span >= k_diversity independent
            sessions.
          * DEMOTE (un-learning): re-checks every ACTIVE rule's own
            recent operational record (rule-scoped, not the tool's
            aggregate) and rolls back the ones whose evidence turned
            against them -- see graxella.gate.health for the criterion
            and why it reacts faster than promotion does.

        No human clicks either way; the evidence is the only
        decision-maker (zero LLM in the loop). Call periodically (after a
        batch, on a timer) from a long-running app. Idempotent.
        """
        from graxella.gate.health import rule_health, should_demote

        self.gate.refresh()
        promoted: list = []
        for interceptor in self.interceptors.values():
            prop = interceptor.standing_proposal()
            if prop is None:
                continue
            verdict, decided = self.gate.decide(prop, by=by)
            if verdict.decision is GateDecision.AUTO_APPROVE:
                promoted.append(self.rulebook.promote(
                    decided, approved_by=by,
                    domain=decided.target.domain or self.domain or "default"))

        demoted: list = []
        for rule in self.rulebook.active_rules():
            health = rule_health(self.memory, rule.id)
            demote, reason = should_demote(health)
            if demote:
                demoted.append(self.rulebook.demote(rule.id, by=by,
                                                     reason=reason))
        return ReconcileResult(promoted=promoted, demoted=demoted)

    def why(self, proposal: Any) -> str:
        """Render the latest gate verdict for a proposal, cited."""
        return self.gate.why(self._pid(proposal))

    # ---------------------------------------------------------- observability

    def stats(self) -> dict:
        """The outcome ledger's aggregate answer (memory.outcome_stats)."""
        return self.memory.outcome_stats()

    def save_topology(self, path: str | Path | None = None,
                      app: Any = None) -> Path:
        """Write the agent-topology map (single-file HTML, no CDN) for
        the last mesh/supervisor built — or an explicit ``app``."""
        app = app or self.app
        if app is None:
            raise RuntimeError(
                "no mesh built yet — call grx.mesh(...) or pass app=")
        out = Path(path) if path else self.workdir / "topology.html"
        out.write_text(render_html(topology_data(app)), encoding="utf-8")
        return out

    def api(self):
        """Build the operator FastAPI app on THIS session's live objects.

        Serves (FastAPI, so Swagger UI is auto-generated at ``/docs``):
          * / and /ui        — the trust center: one tab per graxella
            differentiator (ledger, healing, gate queue with operable
            approve/reject, cited trust, routes, trajectories, value),
            all recomputed from the live session on refresh
          * the control plane — /stats, /trust, /gate/pending,
            /gate/why/{id}, /gate/approve, /gate/reject, /ingest
          * /topology        — the nodes-and-edges agent map as HTML,
            re-rendered from the live ledger on every request
          * /topology/graph  — the same map as JSON (nodes, edges) for
            any renderer (Cytoscape, D3, ...)
          * /tracer/events   — the unified trace stream of the last mesh

        Returns the ASGI app — mount it anywhere, or call ``serve()``.
        """
        from fastapi.responses import HTMLResponse

        from graxella.api.control_plane import create_app
        from graxella.api.trust_center import attach_trust_center

        app = create_app(self.memory, gate=self.gate)
        app.title = f"graxella — {self.name}"
        attach_trust_center(app, self)

        @app.get("/topology", response_class=HTMLResponse)
        def topology_page() -> str:
            if self.app is None:
                return ("<h3>no mesh running — call grx.mesh(...) "
                        "before serving</h3>")
            return render_html(topology_data(self.app))

        @app.get("/topology/graph")
        def topology_graph() -> dict:
            if self.app is None:
                return {"nodes": [], "edges": []}
            return topology_data(self.app)

        @app.get("/tracer/events")
        def tracer_events(limit: int = 100,
                          event_type: str | None = None) -> list[dict]:
            tracer = getattr(self.app, "tracer", None)
            if tracer is None:
                return []
            return [{"seq": e.seq, "ts": e.ts, "source": e.source,
                     "event_type": e.event_type, "payload": e.payload}
                    for e in tracer.events(event_type=event_type,
                                           limit=limit)]

        return app

    def serve(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Serve the operator API for this session (blocking).

        Open http://{host}:{port}/ for the trust center (one tab per
        differentiator, live), /docs for the Swagger UI and /topology
        for the live agent map. Requires graxella[api]
        (fastapi + uvicorn)."""
        try:
            import uvicorn
        except ImportError as exc:
            raise ImportError(
                "grx.serve() needs uvicorn — install graxella[api]") from exc
        uvicorn.run(self.api(), host=host, port=port)

    def __repr__(self) -> str:  # pragma: no cover
        return (f"Session({self.name!r}, domain={self.domain!r}, "
                f"model_id={self.model_id!r}, workdir={str(self.workdir)!r}, "
                f"tools={list(self.tools)})")


__all__ = ["SESSIONS_ROOT", "Session"]
