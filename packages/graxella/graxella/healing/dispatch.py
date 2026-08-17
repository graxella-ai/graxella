"""Tier-1 magic — turn any LangGraph app into a graxella-instrumented one.

Two entry points:

  * `wrap_tools(tools, rulebook, intent=...)`  — explicit.
    Returns a dict {name: routing_wrapper} you plug into your nodes.
    No global state, no monkey-patching. Recommended for library code.

  * `wrap(app, tools, store, rulebook, docs=...)` — full magic.
    Returns a GraxellaApp with the SAME `.invoke()` signature as the
    original compiled graph, but each call:
      1. Attaches a GraxellaCallback so every node run becomes an Episode.
      2. Patches the passed tools' `.invoke` methods for the duration of the
         call so the rulebook decides the destination BEFORE the primary
         runs — a promoted rule bypasses the legacy tool entirely.
      3. If `docs=...` was set, runs DocsMiner on the seed and prints
         reviewable proposals so a human can decide to promote them
         (never auto-applied).

Scope note: patching is per-invocation and reversed in a `finally` block.
Concurrent `.invoke()` calls sharing the same tool instances will race —
run wrapped apps single-threaded, or use `wrap_tools()` for the multi-worker
case where each worker binds its own tool set.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator

from graxella.healing.recipes import TransformRecipe
from graxella.integrations.langgraph import GraxellaCallback
from graxella.rulebook import Rulebook

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool  # noqa: F401

    from graxella.knowledge.seed import KnowledgeSeed  # noqa: F401
    from graxella.memory import ExperienceStore  # noqa: F401


# --------------------------------------------------------------- routing shim
def _recipe_from(rule_recipe: dict[str, Any]) -> TransformRecipe:
    return TransformRecipe(
        field_map=dict(rule_recipe.get("field_map", {})),
        static_defaults=dict(rule_recipe.get("static_defaults", {})),
        drop_fields=tuple(rule_recipe.get("drop_fields", ())),
    )


def _route_once(tools_by_name: dict[str, "BaseTool"],
                name: str,
                args: dict[str, Any],
                *,
                rulebook: Rulebook | None,
                intent: str | None,
                config: Any) -> Any:
    """Consult rulebook; call approved successor if any, else call primary."""
    if rulebook is not None:
        rule = rulebook.find_substitution(name, intent=intent)
        if rule is not None and rule.with_skill in tools_by_name:
            recipe = _recipe_from(rule.recipe)
            translated = recipe.apply(args)
            return tools_by_name[rule.with_skill].invoke(translated, config=config)
    return tools_by_name[name].invoke(args, config=config)


# --------------------------------------------------------------- wrap_tools
def wrap_tools(tools: Iterable["BaseTool"], *,
               rulebook: Rulebook | None = None,
               intent: str | None = None) -> dict[str, Callable[..., Any]]:
    """Return {tool_name: routed_callable}. Explicit: use these in your nodes.

    Each returned callable has the same call signature as a BaseTool's
    `.invoke(args, config=None)`, so a node can drop it in without further
    code changes.
    """
    tools_by_name: dict[str, "BaseTool"] = {t.name: t for t in tools}

    def _make(name: str) -> Callable[..., Any]:
        def _routed(args: dict[str, Any], config: Any = None) -> Any:
            return _route_once(tools_by_name, name, args,
                               rulebook=rulebook, intent=intent, config=config)
        _routed.__name__ = f"routed_{name}"
        return _routed

    return {name: _make(name) for name in tools_by_name}


# --------------------------------------------------------------- wrap (magic)
@contextmanager
def _patch_tool_invokes(tools_by_name: dict[str, "BaseTool"],
                        rulebook: Rulebook | None,
                        intent: str | None) -> Iterator[None]:
    """Temporarily replace each tool's `.invoke` with a rulebook-aware version.

    Uses `object.__setattr__` / `__delattr__` to bypass pydantic's guard on
    BaseTool. Restoration in `finally` — if this raises after patching, the
    tools stay patched, so wrap this whole context in your own try/finally
    if you need airtight cleanup.

    Intent semantics: `intent` here is treated as a HINT, not a filter. If
    the caller passes a value, we first try an intent-scoped lookup; if that
    misses, we retry with intent=None (any-intent match). This keeps `wrap()`
    ergonomic — a promoted rule applies regardless of the wrapper's intent
    tag. `wrap_tools()` remains available for callers that need strict
    intent-scoped routing.
    """
    patched: list[str] = []
    try:
        for name, tool in tools_by_name.items():
            original_invoke = type(tool).invoke.__get__(tool, type(tool))

            def _routed(input_: Any, config: Any = None,
                        _name: str = name,
                        _orig: Callable[..., Any] = original_invoke) -> Any:
                # LangGraph's ToolNode calls `tool.invoke(tool_call, ...)`
                # where `tool_call` is a dict shaped
                #   {"name": ..., "args": {...}, "id": ..., "type": "tool_call"}
                # A caller using the tool directly passes just the args dict.
                # Detect and unwrap so `recipe.apply` sees the actual args.
                is_tool_call = (isinstance(input_, dict)
                                and input_.get("type") == "tool_call"
                                and isinstance(input_.get("args"), dict))
                args = input_["args"] if is_tool_call else input_

                if rulebook is not None:
                    rule = rulebook.find_substitution(_name, intent=intent)
                    if rule is None and intent is not None:
                        rule = rulebook.find_substitution(_name, intent=None)
                    if rule is not None and rule.with_skill in tools_by_name:
                        recipe = _recipe_from(rule.recipe)
                        translated = recipe.apply(args)
                        target = tools_by_name[rule.with_skill]
                        if is_tool_call:
                            new_tc = dict(input_)
                            new_tc["args"] = translated
                            new_tc["name"] = target.name
                            return target.invoke(new_tc, config=config)
                        return target.invoke(translated, config=config)
                return _orig(input_, config=config)

            object.__setattr__(tool, "invoke", _routed)
            patched.append(name)
        yield
    finally:
        for name in patched:
            try:
                object.__delattr__(tools_by_name[name], "invoke")
            except AttributeError:
                pass


@dataclass
class GraxellaApp:
    """Wrapper around a compiled LangGraph app that adds observability +
    rulebook consultation transparently. Same `.invoke()` signature as
    the underlying app — swap it in without touching node code."""

    app: Any
    tools_by_name: dict[str, "BaseTool"]
    store: "ExperienceStore"
    rulebook: Rulebook | None = None
    default_intent: str = "dispatch"
    session_prefix: str = "grx"
    _call_seq: int = field(default=0, init=False)

    def _next_session(self) -> str:
        self._call_seq += 1
        return f"{self.session_prefix}-{self._call_seq}"

    def invoke(self, state: Any, config: Any = None) -> Any:
        config = dict(config or {})
        callbacks = list(config.get("callbacks") or [])
        callbacks.append(GraxellaCallback(
            store=self.store,
            session_id=self._next_session(),
            default_intent=self.default_intent,
        ))
        config["callbacks"] = callbacks

        with _patch_tool_invokes(self.tools_by_name, self.rulebook,
                                 self.default_intent):
            return self.app.invoke(state, config=config)


def wrap(app: Any, *,
         tools: Iterable["BaseTool"],
         store: "ExperienceStore",
         rulebook: str | Path | Rulebook | None = None,
         docs: str | Path | Iterable[str | Path] | None = None,
         intent: str = "dispatch",
         session_prefix: str = "grx") -> GraxellaApp:
    """One-line magic — wrap `app` so every call is intelligent.

    Parameters
    ----------
    app       : a compiled LangGraph app (anything with `.invoke(state, config)`)
    tools     : the LangChain BaseTools this app dispatches
    store     : ExperienceStore — every call yields an Episode
    rulebook  : path OR loaded Rulebook. If None, only observability.
    docs      : optional path(s) to a docs tree — if given, DocsMiner runs
                once at wrap-time and prints reviewable proposals to stderr.
                Nothing is auto-promoted: promotion remains human-gated.
    intent    : recorded on every Episode + used to scope rulebook lookups
    """
    tools_by_name = {t.name: t for t in tools}

    rb: Rulebook | None
    if isinstance(rulebook, (str, Path)):
        rb = Rulebook(path=Path(rulebook))
    else:
        rb = rulebook

    if docs is not None:
        _print_docs_proposals(docs, default_intent=intent, rulebook=rb)

    return GraxellaApp(
        app=app,
        tools_by_name=tools_by_name,
        store=store,
        rulebook=rb,
        default_intent=intent,
        session_prefix=session_prefix,
    )


def _print_docs_proposals(docs: str | Path | Iterable[str | Path], *,
                          default_intent: str,
                          rulebook: Rulebook | None) -> None:
    """Ingest docs, mine, print proposals to stderr — never promote.

    Deferred import: `from_docs` and `DocsMiner` sit in modules that pull
    ingest + agenda. Keeping the import local means importing
    `graxella.healing.dispatch` alone doesn't trigger those dependencies.
    """
    from graxella.agenda import DocsMiner
    from graxella.knowledge import from_docs

    seed = from_docs(docs)
    proposals = DocsMiner(seed=seed, default_intent=default_intent).mine([])
    if not proposals:
        return

    approved = {r.proposal_id for r in (rulebook.all_rules() if rulebook else [])}
    pending = [p for p in proposals if p.id not in approved]
    if not pending:
        return

    print(f"[graxella] {len(pending)} docs-derived proposal(s) awaiting review:",
          file=sys.stderr)
    for p in pending:
        print(f"  - {p.id} [{p.kind}] {p.subject}  conf={p.confidence:.2f}",
              file=sys.stderr)
    print("[graxella] promote with: graxella agenda promote --id <prop_id> ...",
          file=sys.stderr)
