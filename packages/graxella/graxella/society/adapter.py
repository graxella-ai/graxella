"""graxella.society.adapter — thin wrap around agent2society.Mesh.

Exposes a graxella-shaped ``Society`` that owns:
  * the underlying agent2society Mesh (routing + governance + dispatch)
  * a JSONL explanation store that doubles as the routing audit log
  * governance hooks wired to the graxella tracer (detection-only,
    never auto-corrects -- this is one of the load-bearing design
    principles: silent remediation destroys auditability)

Every ``Society.route(task)`` produces a RoutingExplanation which is:
  1. Persisted through the ExplanationStore.
  2. Emitted as a tracer event ("route").
  3. Optionally written into the Memory as an orchestration decision.

Beat 1 uses ``Mesh.run(...)`` under the hood so plain-callable agents
dispatched via the LocalTransport are exercised end-to-end.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

_log = logging.getLogger("graxella")

from agent2society import (
    Handoff,
    JsonlFileStore,
    Mesh,
    RoutingExplanation,
)


TracerHook = Callable[[str, dict], None]


@dataclass
class RouteResult:
    """The graxella-shaped return of a ``Society.route(...)`` call."""

    chosen_agent: str | None
    chosen_skill: str | None
    score: float
    margin: float
    flags: tuple[str, ...]
    rationale: str
    alternatives: list[dict]
    handoff_id: str
    response: str
    explanation: RoutingExplanation
    # Dispatch telemetry (task 0A-2) — feeds the typed outcome record.
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tools_used: list[str] = field(default_factory=list)  # task 1-7


class _CallableCard:
    """Lightweight wrapper so ``agent2society.CallableAdapter`` picks a
    plain callable up with the caller-supplied name + skill tags.

    CallableAdapter looks for ``.name``, ``.description``, ``.skills``
    attributes (or falls back to ``__name__`` / ``__doc__``). We satisfy
    the shape here so ``Mesh.add(wrapper)`` yields the desired card.
    """

    def __init__(self, name: str, runner: Callable[..., Any],
                 skills: list[str], description: str = "") -> None:
        self.name = name
        self.description = description or f"{name} agent"
        # Each skill string becomes an id + name + tag set. Router uses
        # the tags for TF-IDF matching, so we split words into tags.
        self.skills = [
            {
                "id": _slug(s),
                "name": s,
                "description": s,
                "tags": _split_tags(s),
            }
            for s in (skills or [f"{name}.default"])
        ]
        self._runner = runner
        # Last-call telemetry (task 0A-2). The mesh's dispatch returns a
        # plain string, so token usage rides out-of-band on the card and
        # Society.route() picks it up right after dispatch. Per-call slot,
        # not per-thread — concurrency-safe dispatch is Phase 3 (task 3-4).
        self.last_usage: dict | None = None
        # Dispatch-time context cell shared with the owning Society
        # (task 0B-2): [0] holds the recall block for the current dispatch.
        self.context_slot: list[str] | None = None
        self.last_recall: str = ""  # what was active during the last call
        self.last_tool_calls: list[str] = []  # tool names, last dispatch (1-7)

    def __call__(self, payload: Any) -> Any:  # picked up as runner
        self.last_usage = None
        self.last_tool_calls = []
        self.last_recall = self.context_slot[0] if self.context_slot else ""
        result = self._runner(payload)
        if isinstance(result, dict):
            usage = result.get("usage")
            if isinstance(usage, dict):
                self.last_usage = usage
            calls = result.get("tool_calls")
            if isinstance(calls, list):
                self.last_tool_calls = [
                    str(tc.get("name")) for tc in calls
                    if isinstance(tc, dict) and tc.get("name")
                ][:10]
            # Telemetry captured — the mesh response is the TEXT, never
            # the runner's envelope dict (a dict repr is not a reply).
            if "result" in result:
                return result["result"]
        return result


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s).strip("_").lower() or "skill"


def _split_tags(s: str) -> list[str]:
    return [w.lower() for w in s.replace(",", " ").split() if len(w) > 1]


def _looks_like_langgraph_agent(obj: Any) -> bool:
    """True for a ``CompiledStateGraph`` from ``create_react_agent`` /
    ``create_agent``. Detected by the shape (``.nodes`` dict + ``.invoke``
    method + non-empty ``.name``), not by isinstance -- so it works
    across langgraph and langchain.agents versions."""
    return (hasattr(obj, "nodes") and hasattr(obj, "invoke")
            and callable(getattr(obj, "invoke", None))
            and bool(getattr(obj, "name", "")))


def _looks_like_crewai_agent(obj: Any) -> bool:
    """Duck-typed check for ``crewai.Agent`` shape: ``.role`` + ``.tools``,
    not callable itself."""
    return hasattr(obj, "role") and hasattr(obj, "tools") and not callable(obj)


def _extract_langgraph_tools(agent: Any) -> list[Any]:
    """Pull the bound tools out of a compiled react-agent graph. Looks
    for a ``tools`` node whose bound runnable is a ToolNode exposing
    ``tools_by_name``. Returns [] if the shape doesn't match — LOUDLY
    (0C-3): without tools, this agent's skills degrade to name-only tags
    and routing quality silently drops, so the operator must know."""
    try:
        tools_node = agent.nodes.get("tools")
        if tools_node is None:
            return []
        bound = getattr(tools_node, "bound", None)
        by_name = getattr(bound, "tools_by_name", None)
        if isinstance(by_name, dict):
            return list(by_name.values())
        _log.warning(
            "graxella: could not introspect tools for agent %r — the "
            "ToolNode shape has changed (langgraph version drift?). Its "
            "skills degrade to name-only tags; routing quality suffers.",
            getattr(agent, "name", "?"))
    except Exception as exc:
        _log.warning(
            "graxella: tool introspection raised %s for agent %r — "
            "skills degrade to name-only tags. Pin langgraph or report "
            "the shape.", type(exc).__name__, getattr(agent, "name", "?"))
    return []


def _langgraph_agent_info(agent: Any) -> tuple[str, list[Any], list[str]]:
    """(name, tool_objects, skill_tag_strings) for a compiled LangGraph
    agent. Skill tags are: agent name + one line per tool description."""
    name = _slug(getattr(agent, "name", "") or "agent")
    tools = _extract_langgraph_tools(agent)
    tags: list[str] = [f"{name} agent"]
    for t in tools:
        desc = getattr(t, "description", None)
        tname = getattr(t, "name", None) or getattr(t, "__name__", None)
        if desc:
            tags.append(str(desc).strip().split("\n")[0])
        elif tname:
            tags.append(str(tname).replace("_", " "))
    return name, tools, tags


def _usage_from_messages(messages: list) -> dict | None:
    """Sum LangChain ``usage_metadata`` across a run's messages.

    Returns {"input_tokens": int, "output_tokens": int} or None when no
    message carried usage (stub agents, non-LLM runners). Task 0A-2: this
    is where the token cost of a dispatch becomes ledger-recordable.
    """
    tin = tout = 0
    seen = False
    for m in messages:
        um = getattr(m, "usage_metadata", None)
        if isinstance(um, dict):
            tin += int(um.get("input_tokens") or 0)
            tout += int(um.get("output_tokens") or 0)
            seen = True
    return {"input_tokens": tin, "output_tokens": tout} if seen else None


def _make_langgraph_runner(agent: Any, peer_context: str = "",
                           context_slot: list[str] | None = None) -> Callable[[Any], Any]:
    """Wrap a compiled LangGraph agent so its ``.invoke`` fits the
    Society dispatch shape. ``peer_context`` is the static L0 peer
    directory; ``context_slot`` is the per-dispatch cell (case recall,
    task 0B-2) read at call time so injection never touches routing."""
    def runner(payload: Any) -> dict:
        task = str(payload)
        msgs: list[Any] = []
        if peer_context:
            msgs.append(("system", peer_context))
        if context_slot and context_slot[0]:
            msgs.append(("system", context_slot[0]))
        msgs.append(("user", task))
        result = agent.invoke({"messages": msgs})
        messages = result.get("messages", []) if isinstance(result, dict) else []
        final = messages[-1] if messages else None
        content = getattr(final, "content", "") if final else ""
        tool_calls: list[dict] = []
        for m in messages:
            for tc in (getattr(m, "tool_calls", None) or []):
                tool_calls.append({
                    "name": tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None),
                    "args": tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None),
                })
        out: dict = {"result": content, "tool_calls": tool_calls}
        usage = _usage_from_messages(messages)
        if usage:
            out["usage"] = usage
        return out
    return runner


def _crewai_runner(agent: Any) -> Callable[[Any], Any]:
    """Fallback runner for crewai / langchain-classic agent objects."""
    for method in ("kickoff", "invoke", "run"):
        fn = getattr(agent, method, None)
        if callable(fn):
            return lambda payload, _f=fn: {"result": _f(str(payload))}
    tools = getattr(agent, "tools", []) or []
    if tools:
        first = tools[0]
        if hasattr(first, "invoke"):
            return lambda payload: {"result": first.invoke(payload)}
        return first
    return lambda payload: {"result": f"{getattr(agent, 'role', 'agent')}: {payload}"}


def _crewai_skills(agent: Any) -> list[str]:
    """Extract skill-tag strings from a crewai-shaped object."""
    tags: list[str] = []
    goal = getattr(agent, "goal", None)
    if goal:
        tags.append(str(goal))
    for t in getattr(agent, "tools", []) or []:
        desc = getattr(t, "description", None)
        name = getattr(t, "name", None) or getattr(t, "__name__", None)
        if desc:
            tags.append(str(desc).strip().split("\n")[0])
        elif name:
            tags.append(str(name).replace("_", " "))
    if not tags:
        tags.append(getattr(agent, "role", "agent"))
    return tags


@dataclass
class Society:
    """Deterministic routing + governance for an instrumented graph.

    Construct once per graph, pass into ``graxella.instrument(...)``.
    Register agents via ``add(name, callable, skills=[...])``. Every
    subsequent ``route(task)`` returns a scored, explained, audited
    RouteResult.
    """

    store_path: str = "./graxella-routes.jsonl"
    embed_fn: Any | None = None
    min_score: float = 0.05
    strict: bool = True
    conflict_window: int = 50
    _mesh: Mesh | None = field(default=None, init=False, repr=False)
    _store: JsonlFileStore | None = field(default=None, init=False, repr=False)
    _tracer_hooks: list[TracerHook] = field(default_factory=list, init=False, repr=False)
    _cards: dict[str, "_CallableCard"] = field(default_factory=dict, init=False, repr=False)
    # Dispatch-time context cell (task 0B-2). Case recall is injected
    # HERE — after routing chose, before the agent runs — never into the
    # task string, so the router's scoring is untouched by recall.
    _recall_slot: list[str] = field(default_factory=lambda: [""], init=False, repr=False)

    def __post_init__(self) -> None:
        Path(self.store_path).parent.mkdir(parents=True, exist_ok=True)
        self._store = JsonlFileStore(self.store_path)
        self._mesh = Mesh(
            embed_fn=self.embed_fn,
            min_score=self.min_score,
            strict=self.strict,
            conflict_window=self.conflict_window,
            store=self._store,
        )
        # Wire governance detectors to our tracer. These are detection-only:
        # each fires the tracer with a structured signal; no auto-remediation.
        self._mesh.on_conflict(lambda evt: self._emit("governance.conflict", _conflict_payload(evt)))
        self._mesh.on_capability_drift(lambda evt: self._emit("governance.drift", _drift_payload(evt)))
        self._mesh.on_low_confidence(lambda expl: self._emit("governance.low_confidence", _expl_payload(expl)))
        self._mesh.on_low_margin(lambda expl: self._emit("governance.low_margin", _expl_payload(expl)))
        self._mesh.on_human_review(lambda expl, result: self._emit(
            "governance.human_review",
            {"explanation": _expl_payload(expl), "result": str(result)[:500]},
        ))

    # -- construction helpers ------------------------------------------------

    def add(self, name_or_agent: Any, card_or_callable: Any = None, *,
            skills: list[str] | None = None) -> None:
        """Register an agent with the mesh.

        Call shapes (introspected by type -- no new syntax to learn):
          * ``add(langgraph_agent)`` -- a compiled graph from
            ``langgraph.prebuilt.create_react_agent(...)`` or
            ``langchain.agents.create_agent(...)``. Name comes from the
            agent's ``.name``, skills from bound tool descriptions.
          * ``add(crewai_agent)`` -- any object with ``.role`` + ``.tools``.
            Runner tries ``.kickoff`` -> ``.invoke`` -> ``.run`` in order.
          * ``add(name, callable, skills=[...])`` -- classic form for
            plain callables (stubs, deterministic agents, tests).
          * ``add(name, agent_card_or_url)`` -- passed straight through
            to the underlying mesh (for A2A remote agents).
        """
        if card_or_callable is None and _looks_like_langgraph_agent(name_or_agent):
            agent = name_or_agent
            name, _, agent_skills = _langgraph_agent_info(agent)
            runner = _make_langgraph_runner(agent)
            source: Any = _CallableCard(name=name, runner=runner, skills=agent_skills)
            self._register_card(source)
            return

        if card_or_callable is None and _looks_like_crewai_agent(name_or_agent):
            agent = name_or_agent
            name = _slug(getattr(agent, "role", "agent"))
            runner = _crewai_runner(agent)
            agent_skills = _crewai_skills(agent)
            source = _CallableCard(name=name, runner=runner, skills=agent_skills)
            self._register_card(source)
            return

        name = name_or_agent
        source = card_or_callable
        if callable(source) and not hasattr(source, "skills"):
            source = _CallableCard(name=name, runner=source, skills=skills or [])
        self._register_card(source)

    def _register_card(self, source: Any) -> None:
        """Add to the mesh AND remember _CallableCards by name so route()
        can read their last-call telemetry (task 0A-2). All registration
        paths — including graxella.mesh's — must come through here."""
        if isinstance(source, _CallableCard):
            self._cards[source.name] = source
            source.context_slot = self._recall_slot
        self._mesh.add(source)

    def _register_cards(self, sources: list) -> None:
        """Batch registration: one copy-on-write cycle for the whole
        batch (3-4 perf fix — Mesh.add is O(n) deepcopy per call)."""
        for source in sources:
            if isinstance(source, _CallableCard):
                self._cards[source.name] = source
                source.context_slot = self._recall_slot
        self._mesh.add_many(sources)

    def add_with_peer_context(self, agent: Any, peer_context: str) -> None:
        """Register a LangGraph agent with a peer directory injected as a
        system message on every invocation. Used by ``graxella.mesh(...)``
        so agents can suggest handoffs to each other."""
        if not _looks_like_langgraph_agent(agent):
            self.add(agent)
            return
        name, _, agent_skills = _langgraph_agent_info(agent)
        runner = _make_langgraph_runner(agent, peer_context=peer_context)
        self._register_card(_CallableCard(name=name, runner=runner, skills=agent_skills))

    def attach_tracer(self, hook: TracerHook) -> None:
        """Register a tracer callback that fires on every routing decision."""
        self._tracer_hooks.append(hook)

    # -- the core operation --------------------------------------------------

    def route(self, task: str, *, intent: str = "",
              confidence_required: float = 0.0,
              assumptions: list[str] | None = None,
              recall_context: str | None = None) -> RouteResult:
        """Route ``task`` deterministically. Dispatches through the mesh's
        LocalTransport / HttpTransport and returns an explained RouteResult.

        ``recall_context`` (task 0B-2) is injected into the CHOSEN agent's
        context at dispatch time via the shared slot — it never enters the
        task string, so routing scores are recall-blind by construction.
        """
        handoff = Handoff(
            task=task,
            intent=intent,
            assumptions=assumptions or [],
            confidence_required=confidence_required,
        )
        t0 = time.perf_counter()
        # Disclosure L1 (task 2-3): pre-route for the shortlist, then hand
        # the likely winner one-line summaries of its runner-up peers —
        # informed handoffs at flat cost (top-k only, however big the mesh).
        l1_block = ""
        try:
            shortlist = self._mesh.route(task, top_k=3)
            l1_block = _build_l1_block(shortlist, self._cards)
        except Exception as exc:
            _log.warning("graxella: L1 disclosure skipped: %s", exc)
        self._recall_slot[0] = "\n\n".join(
            b for b in (recall_context or "", l1_block) if b)
        try:
            response = self._mesh.run(handoff)
        finally:
            self._recall_slot[0] = ""
        latency_ms = (time.perf_counter() - t0) * 1000.0
        explanation = self._mesh.explain(handoff.id)
        # Mesh.run() always writes an explanation, even on unroutable /
        # conformance-blocked paths. Defensive fallback in case a custom
        # store returns None.
        if explanation is None:
            raise RuntimeError(f"no explanation stored for handoff {handoff.id}")

        # Token usage rides out-of-band on the dispatched card (0A-2).
        card = self._cards.get(explanation.chosen_agent or "")
        usage = card.last_usage if card is not None else None

        result = RouteResult(
            chosen_agent=explanation.chosen_agent,
            chosen_skill=explanation.chosen_skill,
            score=float(explanation.confidence),
            margin=float(getattr(explanation, "margin", 0.0)),
            flags=tuple(getattr(explanation, "flags", ()) or ()),
            rationale=str(getattr(explanation, "rationale", "") or ""),
            alternatives=[
                {"agent": a.agent, "skill": a.skill_id, "score": float(a.score)}
                for a in (getattr(explanation, "alternatives", None) or [])
            ],
            handoff_id=handoff.id,
            response=response or "",
            explanation=explanation,
            latency_ms=latency_ms,
            tokens_in=(usage or {}).get("input_tokens"),
            tokens_out=(usage or {}).get("output_tokens"),
            tools_used=list(card.last_tool_calls) if card is not None else [],
        )
        self._emit("route", {
            "handoff_id": handoff.id,
            "task": task,
            "intent": intent,
            "chosen_agent": result.chosen_agent,
            "chosen_skill": result.chosen_skill,
            "score": result.score,
            "margin": result.margin,
            "flags": list(result.flags),
            "alternatives": result.alternatives,
            "latency_ms": round(latency_ms, 2),
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
        })
        return result

    # -- read-side (forwarded to Mesh) --------------------------------------

    def explanations(self, *, limit: int | None = None) -> list[RoutingExplanation]:
        exps = list(self._mesh.explanations())
        return exps if limit is None else exps[-limit:]

    def agents(self) -> list[str]:
        return list(self._mesh.agents())

    def optimize(self, *, labeled: list) -> Any:
        """Run the B11 optimizer -- backtest-gated skill tag mining."""
        return self._mesh.optimize(labeled)

    def report(self) -> str:
        return self._mesh.report(render=False)

    def describe(self) -> list[dict]:
        return self._mesh.describe()

    # -- internal ------------------------------------------------------------

    def _emit(self, event_type: str, payload: dict) -> None:
        for hook in self._tracer_hooks:
            try:
                hook(event_type, payload)
            except Exception as exc:
                # Observability must never break routing — but a broken
                # tracer hook is itself an observability outage (0C-3).
                _log.warning("graxella: tracer hook failed on %r: %s: %s",
                             event_type, type(exc).__name__, exc)


#: Disclosure-spec L1 budget: ≤120 tokens ≈ 480 chars for the whole block.
_L1_MAX_CHARS = 480


def _build_l1_block(shortlist: list, cards: dict) -> str:
    """Tier L1 (docs/specs/disclosure-spec.md): one-line skill summaries
    for the routing runner-ups, injected into the WINNER's context so its
    handoff choices are informed. Router-driven, deterministic, bounded —
    cost is flat in mesh size because only the shortlist is disclosed."""
    if len(shortlist) < 2:
        return ""
    winner = shortlist[0].agent
    lines = ["Peers ranked most relevant to this task (for handoffs):"]
    for cand in shortlist[1:3]:
        if cand.agent == winner:
            continue
        card = cards.get(cand.agent)
        skills = "; ".join(s["name"] for s in (card.skills if card else []))[:90]
        lines.append(f"  - {cand.agent}: {skills or cand.skill_id}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)[:_L1_MAX_CHARS]


def _conflict_payload(evt: Any) -> dict:
    return {
        "kind": getattr(evt, "kind", "conflict"),
        "detail": getattr(evt, "detail", ""),
        "handoff_ids": list(getattr(evt, "handoff_ids", []) or []),
        "agents": list(getattr(evt, "agents", []) or []),
    }


def _drift_payload(evt: Any) -> dict:
    return {
        "agent": getattr(evt, "agent", ""),
        "skills_seen": list(getattr(evt, "skills_seen", []) or []),
        "sample_handoff_ids": list(getattr(evt, "sample_handoff_ids", []) or []),
    }


def _expl_payload(expl: RoutingExplanation) -> dict:
    return {
        "chosen_agent": getattr(expl, "chosen_agent", ""),
        "chosen_skill": getattr(expl, "chosen_skill", ""),
        "score": float(getattr(expl, "confidence", 0.0)),
        "margin": float(getattr(expl, "margin", 0.0)),
        "flags": list(getattr(expl, "flags", ()) or ()),
    }
