"""graxella.mesh -- wrap a collection of native LangGraph / LangChain
agents in a graxella-governed mesh WITHOUT introducing a new agent class.

Users write plain framework code:

    from langgraph.prebuilt import create_react_agent
    from langchain_ollama import ChatOllama
    import graxella

    llm = ChatOllama(model="qwen2.5:3b", temperature=0)
    triage    = create_react_agent(llm, [check_order, lookup_policy], name="triage")
    responder = create_react_agent(llm, [draft_email],                 name="responder")

    app = graxella.mesh(
        [triage, responder],
        memory=graxella.Memory.sqlite("./mnema.db"),
    )
    app.invoke({"messages": [("user", "refund order 1234, arrived damaged")]})

Two flavours:

  * ``graxella.mesh(agents, ...)``       -- deterministic TF-IDF routing.
    Zero runtime-LLM cost for picking the next agent. This is graxella's
    differentiator: compile-time LLM > runtime LLM.

  * ``graxella.supervisor(agents, model, ...)`` -- LLM-driven routing.
    A supervisor LLM sees the peer directory and picks. Slower + more
    flexible; matches the ``langgraph-supervisor`` pattern.

Both:
  * Inject a peer directory as a system message on every agent call so
    each agent knows what its neighbours can do (agents "know each
    other's limitations" and can plan handoffs).
  * Wrap the whole thing in ``instrument(...)``, so ``.memory``,
    ``.tracer``, ``.gate``, ``.constitution``, ``.route()``, ``.why()``
    all work on the returned object.
  * Expose the LangGraph Runnable surface (``.invoke``, ``.stream``,
    ``.batch``) so the wrap is a drop-in replacement for a single graph.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Iterable

from graxella.beliefs import Memory
from graxella.constitution import Constitution
from graxella.gate import PromotionGate
from graxella.instrument import InstrumentedApp, instrument
from graxella.society import Society
from graxella.society.adapter import (
    _CallableCard,
    _crewai_runner,
    _crewai_skills,
    _langgraph_agent_info,
    _looks_like_crewai_agent,
    _looks_like_langgraph_agent,
    _make_langgraph_runner,
    _slug,
)
from graxella.tracer import UnifiedTracer


# ---------------- agent-shape normalisation ----------------------------

def _describe_agent(agent: Any) -> dict:
    """Normalise any supported agent shape into a common descriptor:
        {name, role, tool_names, tool_descs}

    Used to build the peer directory that every agent sees.
    """
    # graxella.Agent (has _routing_name + tools + role)
    if hasattr(agent, "_routing_name") and hasattr(agent, "tools"):
        name = agent._routing_name()
        tools = getattr(agent, "tools", []) or []
        return {
            "name": name,
            "role": getattr(agent, "role", name),
            "tool_names": [getattr(t, "name", "?") for t in tools],
            "tool_descs": [
                (getattr(t, "description", "") or "").strip().split("\n")[0]
                for t in tools
            ],
        }
    if _looks_like_langgraph_agent(agent):
        name, tools, _skills = _langgraph_agent_info(agent)
        return {
            "name": name,
            "role": name.replace("_", " "),
            "tool_names": [getattr(t, "name", "?") for t in tools],
            "tool_descs": [
                (getattr(t, "description", "") or "").strip().split("\n")[0]
                for t in tools
            ],
        }
    if _looks_like_crewai_agent(agent):
        name = _slug(getattr(agent, "role", "agent"))
        tools = getattr(agent, "tools", []) or []
        return {
            "name": name,
            "role": getattr(agent, "role", name),
            "tool_names": [getattr(t, "name", "?") for t in tools],
            "tool_descs": [
                (getattr(t, "description", "") or "").strip().split("\n")[0]
                for t in tools
            ],
        }
    name = getattr(agent, "__name__", None) or getattr(agent, "name", "agent")
    return {"name": _slug(name), "role": str(name), "tool_names": [], "tool_descs": []}


def _build_peer_context(descs: list[dict], self_name: str | None = None) -> str:
    """Compose the peer directory system message. Excludes ``self_name``
    so the LLM doesn't see itself in the delegation list.

    Disclosure Spec v0.1: this is tier L0 (name + one-line capabilities,
    always injected, bounded per agent). L1-L3 land in Phase 2 task 2-3 —
    see docs/specs/disclosure-spec.md before extending what is revealed
    here: additional detail belongs in a higher tier, not in L0."""
    lines = ["You are one agent in a graxella mesh. Your peers and their capabilities:"]
    for d in descs:
        if self_name and d["name"] == self_name:
            continue
        tools_str = ", ".join(d["tool_names"]) or "(no tools)"
        lines.append(f"  - {d['name']}: {tools_str}")
    lines.append("")
    lines.append("If a request would be handled better by a peer, say so explicitly "
                 "in your response so the operator can re-route. Otherwise, do your job.")
    return "\n".join(lines)


def _register(society: Society, agent: Any, descs: list[dict]) -> str:
    """Register one agent into a Society with peer-awareness baked into
    its runner. Returns the routing name.

    Detection order:
      1. graxella.Agent   -- has ``_make_runner(peer_context=...)``.
      2. Native LangGraph -- CompiledStateGraph from create_react_agent.
      3. CrewAI-shaped    -- ``.role`` + ``.tools`` object (peer context
         is not injectable through crewai's own prompt, so we skip it).
      4. Bare callable    -- registered by ``__name__``.
    """
    # (1) graxella.Agent
    if hasattr(agent, "_make_runner") and hasattr(agent, "_routing_name") \
            and hasattr(agent, "_skill_tags"):
        name = agent._routing_name()
        peer_ctx = _build_peer_context(descs, self_name=name)
        runner = agent._make_runner(peer_context=peer_ctx,
                                    context_slot=society._recall_slot)
        society._register_card(_CallableCard(name=name, runner=runner, skills=agent._skill_tags()))
        return name
    # (2) native LangGraph
    if _looks_like_langgraph_agent(agent):
        name, _, skills = _langgraph_agent_info(agent)
        peer_ctx = _build_peer_context(descs, self_name=name)
        runner = _make_langgraph_runner(agent, peer_context=peer_ctx,
                                        context_slot=society._recall_slot)
        society._register_card(_CallableCard(name=name, runner=runner, skills=skills))
        return name
    # (3) crewai-shaped
    if _looks_like_crewai_agent(agent):
        name = _slug(getattr(agent, "role", "agent"))
        runner = _crewai_runner(agent)
        society._register_card(_CallableCard(name=name, runner=runner, skills=_crewai_skills(agent)))
        return name
    # (4) bare callable — the docstring's first line is its capability
    # card (cards derive from code, never from user-authored config).
    name = _slug(getattr(agent, "__name__", None) or "agent")
    skills = [name.replace("_", " ")]
    doc = (getattr(agent, "__doc__", None) or "").strip()
    if doc:
        skills.append(doc.split("\n")[0].strip())
    society._register_card(_CallableCard(name=name, runner=agent, skills=skills))
    return name


#: Project-local workdir for default stores (task 0C-4). A product whose
#: thesis is "memory is the substrate of governance" never defaults to
#: amnesia — memory persists across runs unless the caller explicitly
#: asks for ephemeral.
GRAXELLA_WORKDIR = Path(".graxella")

_log = logging.getLogger("graxella")


def _default_memory(agent_id: str) -> Memory:
    """Persistent sqlite memory in ./.graxella/ (task 0C-4). Two runs in
    the same project share evidence by default — that's the whole point."""
    GRAXELLA_WORKDIR.mkdir(parents=True, exist_ok=True)
    db = GRAXELLA_WORKDIR / "mnema.db"
    _log.info("graxella: using persistent memory at %s (pass memory=... "
              "to control, memory='ephemeral' for a throwaway store)", db)
    return Memory.sqlite(db_path=str(db), agent_id=agent_id)


def _ephemeral_memory(agent_id: str) -> Memory:
    """Explicit opt-in throwaway store — loudly logged (task 0C-4)."""
    workdir = Path(tempfile.mkdtemp(prefix=f"{agent_id}-"))
    _log.warning("graxella: EPHEMERAL memory at %s — evidence from this run "
                 "is discarded and nothing will be learned from it", workdir)
    return Memory.sqlite(db_path=str(workdir / "mnema.db"), agent_id=agent_id)


def _resolve_memory(memory: Any, agent_id: str) -> Memory:
    if memory is None:
        return _default_memory(agent_id)
    if memory == "ephemeral":
        return _ephemeral_memory(agent_id)
    return memory


# ---------------- router: TF-IDF (default) or small transformer -------

_DEFAULT_TRANSFORMER = "sentence-transformers/all-MiniLM-L6-v2"


def _resolve_router(router: Any) -> Any:
    """Turn the caller's ``router=`` argument into an ``embed_fn``
    compatible with agent2society's Mesh.

    Accepted values:
      * ``None`` or ``"tfidf"`` -- default TF-IDF path (embed_fn=None).
      * ``"transformer"``       -- lazy-load the default MiniLM model
                                   (~90 MB, first call downloads it).
      * any string containing ``"/"`` or ``"-"`` -- treated as a
                                   ``sentence-transformers`` model name.
      * any callable            -- used verbatim as embed_fn.

    Silent failures aren't allowed: if the caller asked for a
    transformer and the package isn't installed, raise a helpful
    ImportError telling them exactly what to install.
    """
    if router is None or router == "tfidf":
        return None
    if callable(router):
        return router
    if isinstance(router, str):
        model_name = _DEFAULT_TRANSFORMER if router == "transformer" else router
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise ImportError(
                f"graxella.mesh(router={router!r}) needs sentence-transformers.\n"
                "Install with:\n"
                "  pip install sentence-transformers\n"
                "or use router='tfidf' (the default -- no extra deps)."
            ) from exc
        model = SentenceTransformer(model_name)

        def embed(text: str) -> list[float]:
            vec = model.encode(text, convert_to_numpy=True)
            return vec.tolist()

        return embed
    raise TypeError(
        f"graxella.mesh(router=...) expects None / 'tfidf' / 'transformer' / "
        f"model-name / callable, got {type(router).__name__}"
    )


# ---------------- mesh runnable (deterministic TF-IDF) ----------------

class _MeshRunnable:
    """Behaves like a LangGraph compiled runnable: ``.invoke`` accepts
    either ``{"messages": [...]}`` or a bare string. Dispatches via
    the InstrumentedApp's full route pipeline (Society TF-IDF +
    Memory.record_decision + Constitution.check)."""

    def __init__(self, society: Society) -> None:
        self.society = society
        self._app: InstrumentedApp | None = None  # set by mesh(...) after instrument()

    def invoke(self, state: Any, config: dict | None = None) -> dict:
        task = _extract_task(state)
        if self._app is not None:
            result, decision_id = self._app.route(task)
        else:
            result = self.society.route(task)
            decision_id = None
        assistant_msg = {"role": "assistant", "content": _stringify(result.response)}
        return {
            "messages": _messages_from(state) + [assistant_msg],
            "route": {
                "agent": result.chosen_agent,
                "skill": result.chosen_skill,
                "score": result.score,
                "strategy": "tfidf",
                "decision_id": decision_id,
            },
        }

    def stream(self, state: Any, config: dict | None = None):
        yield self.invoke(state, config)

    def batch(self, states: list[Any], config: dict | None = None) -> list[dict]:
        return [self.invoke(s, config) for s in states]


# ---------------- supervisor runnable (LLM-driven routing) ------------

class _SupervisorRunnable:
    """LLM picks the next agent from the peer directory, then dispatches
    through the InstrumentedApp so tracer + gate + constitution + memory
    all fire identically to the ``mesh(...)`` path."""

    def __init__(self, society: Society, model: Any,
                 descs: list[dict], prompt: str) -> None:
        self.society = society
        self.model = model
        self.descs = descs
        self.prompt = prompt
        self._names = {d["name"] for d in descs}
        self._app: InstrumentedApp | None = None  # set by supervisor(...) after instrument()

    def invoke(self, state: Any, config: dict | None = None) -> dict:
        task = _extract_task(state)
        picked, pick_err = self._pick(task)
        if pick_err is not None:
            # 0C-3: the caller paid for LLM routing — falling back to
            # TF-IDF silently would hide that they aren't getting it.
            _log.warning("graxella.supervisor: LLM pick failed (%s); "
                         "falling back to deterministic routing", pick_err)
            if self._app is not None:
                self._app.tracer.record(
                    "orchestrator", "degradation.supervisor_fallback",
                    {"task": task[:200], "err": pick_err[:300]},
                )
        biased = f"[route:{picked}] {task}" if picked else task
        if self._app is not None:
            result, decision_id = self._app.route(biased)
        else:
            result = self.society.route(biased)
            decision_id = None
        assistant_msg = {"role": "assistant", "content": _stringify(result.response)}
        return {
            "messages": _messages_from(state) + [assistant_msg],
            "route": {
                "agent": result.chosen_agent,
                "skill": result.chosen_skill,
                "score": result.score,
                "strategy": "supervisor_llm",
                "supervisor_pick": picked,
                "supervisor_fallback": pick_err is not None,
                "decision_id": decision_id,
            },
        }

    def stream(self, state: Any, config: dict | None = None):
        yield self.invoke(state, config)

    def batch(self, states: list[Any], config: dict | None = None) -> list[dict]:
        return [self.invoke(s, config) for s in states]

    def _pick(self, task: str) -> tuple[str | None, str | None]:
        """(picked_name, error). ``error`` is set when the supervisor LLM
        failed outright — the caller emits the degradation event (0C-3).
        An LLM that answered but named no known agent is not an error;
        it's a miss, and deterministic routing takes over quietly."""
        try:
            msg = self.model.invoke(
                [("system", self.prompt), ("user", task)]
            )
            raw = (getattr(msg, "content", "") or "").strip()
            token = raw.split()[0].strip(".,:;\"'`").lower() if raw else ""
            if token in self._names:
                return token, None
            low = raw.lower()
            for n in self._names:
                if n in low:
                    return n, None
            return None, None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"


# ---------------- public API -----------------------------------------

def mesh(agents: Iterable[Any], *,
         memory: Memory | None = None,
         tracer: UnifiedTracer | None = None,
         gate: PromotionGate | None = None,
         constitution: Constitution | None = None,
         router: Any = None,
         store_path: str | None = None,
         domain: str | None = None,
         model_id: str | None = None,
         recall: bool = True) -> InstrumentedApp:
    """Wrap a collection of native agents in a deterministic mesh.

    Each agent gets a peer-directory system message on every call so it
    knows what its neighbours can do. Routing is LLM-free by default --
    deterministic scoring keeps operator audits crisp.

    ``router`` picks the scoring strategy (all still LLM-free at
    dispatch time):
      * ``None`` / ``"tfidf"`` (default) -- word-bag TF-IDF matching.
      * ``"transformer"`` -- small MiniLM (~90 MB, needs
        ``sentence-transformers``). Better generalisation for
        paraphrased / cross-lingual tasks; picks at import time so
        cold-start pays the load once.
      * A model name like ``"BAAI/bge-small-en-v1.5"`` -- your choice
        of sentence-transformers model.
      * A callable ``str -> list[float]`` -- fully custom.

    Returns an ``InstrumentedApp`` (LangGraph-Runnable compatible + all
    graxella accessors: ``.memory``, ``.tracer``, ``.gate``,
    ``.constitution``, ``.route()``, ``.why()``).
    """
    agents = list(agents)
    if not agents:
        raise ValueError("graxella.mesh(agents=...) requires at least one agent")

    descs = [_describe_agent(a) for a in agents]
    memory = _resolve_memory(memory, "graxella-mesh")
    store = store_path or str(GRAXELLA_WORKDIR / "mesh-routes.jsonl")
    Path(store).parent.mkdir(parents=True, exist_ok=True)
    embed_fn = _resolve_router(router)
    society = Society(store_path=store, embed_fn=embed_fn)

    for agent in agents:
        _register(society, agent, descs)

    runnable = _MeshRunnable(society)
    app = instrument(runnable, memory=memory, society=society,
                     tracer=tracer, gate=gate, constitution=constitution,
                     domain=domain, model_id=model_id, recall=recall)
    runnable._app = app  # wire the full route pipeline back in
    return app


def supervisor(agents: Iterable[Any], model: Any, *,
               prompt: str | None = None,
               memory: Memory | None = None,
               tracer: UnifiedTracer | None = None,
               gate: PromotionGate | None = None,
               constitution: Constitution | None = None,
               router: Any = None,
               store_path: str | None = None,
               domain: str | None = None,
               model_id: str | None = None) -> InstrumentedApp:
    """Wrap a collection of native agents with an LLM supervisor.

    ``model`` is the supervisor's own LLM (any LangChain BaseChatModel;
    a small model is fine -- it only picks a name). ``prompt`` is
    optional; if omitted, a default routing prompt is composed from the
    peer directory.

    The chosen agent is dispatched through the same Society plumbing as
    ``mesh(...)`` so tracer / gate / constitution behave identically.
    """
    agents = list(agents)
    if not agents:
        raise ValueError("graxella.supervisor(agents=...) requires at least one agent")

    descs = [_describe_agent(a) for a in agents]
    memory = _resolve_memory(memory, "graxella-supervisor")
    store = store_path or str(GRAXELLA_WORKDIR / "supervisor-routes.jsonl")
    Path(store).parent.mkdir(parents=True, exist_ok=True)
    embed_fn = _resolve_router(router)
    society = Society(store_path=store, embed_fn=embed_fn)

    for agent in agents:
        _register(society, agent, descs)

    default_prompt = prompt or (
        "You are a routing supervisor for a team of specialised agents.\n"
        + _build_peer_context(descs).replace("You are one agent", "The team members are")
        + "\n\nGiven the user's request, reply with the SINGLE agent name best"
          " suited to handle it. Reply with the name only -- no explanation."
    )

    runnable = _SupervisorRunnable(society, model, descs, default_prompt)
    app = instrument(runnable, memory=memory, society=society,
                     tracer=tracer, gate=gate, constitution=constitution,
                     domain=domain, model_id=model_id)
    runnable._app = app
    return app


# ---------------- helpers --------------------------------------------

def _extract_task(state: Any) -> str:
    """Pull the routing task string out of a LangGraph-shaped state or
    a bare string."""
    if isinstance(state, str):
        return state
    if isinstance(state, dict):
        msgs = state.get("messages") or state.get("input") or []
        if isinstance(msgs, str):
            return msgs
        if isinstance(msgs, list) and msgs:
            last = msgs[-1]
            if isinstance(last, tuple) and len(last) >= 2:
                return str(last[1])
            if isinstance(last, dict):
                return str(last.get("content", ""))
            return str(getattr(last, "content", last))
    return str(state)


def _messages_from(state: Any) -> list:
    if isinstance(state, dict):
        m = state.get("messages")
        if isinstance(m, list):
            return list(m)
    return []


def _stringify(x: Any) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return str(x.get("result", x))
    return str(x)
