"""Self-healing tool wrapper.

`heal.wrap(primary, fallback=..., recipe=...)` returns a callable that runs
`primary` first, and on failure translates the args via `recipe` and calls
`fallback`. Both tool invocations propagate LangChain callbacks, so the
GraxellaCallback records BOTH attempts as ToolCalls on the same Episode —
exactly the signal RuleDistiller looks for.

Design principle: compile-time LLM > runtime LLM.
  * Default healing is **deterministic** — a `TransformRecipe` maps source
    fields to target fields at edit time. Zero runtime LLM cost.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig  # noqa: F401
    from langchain_core.tools import BaseTool  # noqa: F401

    from graxella.rulebook import Rulebook  # noqa: F401


#: Casts a recipe can apply after renaming — deliberately small and
#: total (never raises on a plausible value): a heal must never trade a
#: schema-drift failure for a cast-crash failure.
_CASTS: dict[str, Callable[[Any], Any]] = {
    "int": int,
    "float": float,
    "str": str,
    "bool": lambda v: (v if isinstance(v, bool) else
                       str(v).strip().lower() in ("true", "1", "yes", "on")),
}


def _split(path: str) -> list[str]:
    return path.split(".")


def _get(d: dict, path: str) -> tuple[bool, Any]:
    """(found, value) at a dotted path. A flat key ("city") is just a
    one-segment path — existing flat recipes are unaffected."""
    cur: Any = d
    for seg in _split(path):
        if not isinstance(cur, dict) or seg not in cur:
            return False, None
        cur = cur[seg]
    return True, cur


def _pop(d: dict, path: str) -> tuple[bool, Any]:
    segs = _split(path)
    cur: Any = d
    for seg in segs[:-1]:
        if not isinstance(cur, dict) or seg not in cur:
            return False, None
        cur = cur[seg]
    if not isinstance(cur, dict) or segs[-1] not in cur:
        return False, None
    return True, cur.pop(segs[-1])


def _set(d: dict, path: str, value: Any) -> None:
    """Write at a dotted path, coercing any non-dict intermediate into an
    empty dict rather than raising — a heal must never crash because a
    target path collided with an existing scalar."""
    segs = _split(path)
    cur = d
    for seg in segs[:-1]:
        nxt = cur.get(seg)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[seg] = nxt
        cur = nxt
    cur[segs[-1]] = value


@dataclass
class TransformRecipe:
    """Deterministic source->target arg mapping. Applied on failure.

    All four fields accept dotted paths ("user.email") for nested
    dict restructuring; a plain key ("city") is a one-segment path, so
    flat recipes behave exactly as before — nothing here changes existing
    behavior, it only extends what a path can reach.

    field_map        — {"city": "location"} renames/moves a field.
                       {"user.email": "contact.email_address"} restructures.
    static_defaults  — {"units": "metric"} fills a MISSING target field
                       (setdefault semantics, dotted paths supported).
    drop_fields      — ("legacy_flag",) removes fields the target rejects.
    type_casts       — {"age": "int"} coerces a value's type AFTER
                       renaming. One of "int"|"float"|"str"|"bool". A
                       cast that can't apply (bad literal) is skipped,
                       never raised — a failed cast must not turn a
                       recoverable drift into a crash.
    value_map        — {"status": {"active": "ACTIVE"}} remaps specific
                       VALUES after renaming/casting. Values with no entry
                       in the map pass through unchanged.

    Apply order: drop -> rename -> value_map -> type_casts -> defaults.
    """
    field_map: dict[str, str] = field(default_factory=dict)
    static_defaults: dict[str, Any] = field(default_factory=dict)
    drop_fields: tuple[str, ...] = ()
    type_casts: dict[str, str] = field(default_factory=dict)
    value_map: dict[str, dict[Any, Any]] = field(default_factory=dict)

    def apply(self, args: dict[str, Any]) -> dict[str, Any]:
        # Start from a full copy so every untouched field (top-level or
        # nested) survives unchanged; drop and rename then POP their
        # source path out of it and (for renames) write the value back
        # at the target path — dotted paths on either side reach into or
        # build nested structure.
        out: dict[str, Any] = copy.deepcopy(args)
        for path in self.drop_fields:
            _pop(out, path)
        for src, dst in self.field_map.items():
            found, val = _pop(out, src)
            if found:
                _set(out, dst, val)

        for path, mapping in self.value_map.items():
            found, val = _get(out, path)
            if found and val in mapping:
                _set(out, path, mapping[val])

        for path, cast_name in self.type_casts.items():
            found, val = _get(out, path)
            cast = _CASTS.get(cast_name)
            if found and cast is not None:
                try:
                    _set(out, path, cast(val))
                except (TypeError, ValueError):
                    pass   # keep the original value — never crash a heal

        for path, v in self.static_defaults.items():
            if not _get(out, path)[0]:
                _set(out, path, v)
        return out

    def to_dict(self) -> dict[str, Any]:
        """The canonical JSON-safe payload shape — what ships in a
        Proposal's payload and a rulebook rule's ``recipe``. The single
        source ``from_dict`` reverses."""
        return {"field_map": dict(self.field_map),
                "static_defaults": dict(self.static_defaults),
                "drop_fields": list(self.drop_fields),
                "type_casts": dict(self.type_casts),
                "value_map": {k: dict(v) for k, v in self.value_map.items()}}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "TransformRecipe":
        """The single reconstruction path — every site that reads a
        recipe back from the rulebook or a proposal payload uses this,
        so a new recipe capability lands everywhere at once instead of
        being duplicated (and drifting) across call sites."""
        d = d or {}
        return cls(
            field_map=dict(d.get("field_map") or {}),
            static_defaults=dict(d.get("static_defaults") or {}),
            drop_fields=tuple(d.get("drop_fields") or ()),
            type_casts=dict(d.get("type_casts") or {}),
            value_map={k: dict(v) for k, v in (d.get("value_map") or {}).items()},
        )

    def to_proposal(self, *, domain: str, tool: str, origin: str,
                    model_id: str | None = None):
        """Ship this recipe through the unified pipeline (task 1-5): one
        spec.Proposal(kind=transform), gate-decided like everything else.
        Phase 2 (task 2-5) makes this the healer's only exit path."""
        from graxella.gate import spec
        payload = self.to_dict()
        target = spec.TargetScope(domain=domain, tool=tool, model_id=model_id)
        return spec.Proposal(
            id=spec.Proposal.deterministic_id(spec.ArtifactKind.TRANSFORM,
                                              target, payload),
            kind=spec.ArtifactKind.TRANSFORM,
            target=target,
            payload=payload,
            origin=origin,
        )


class HealedTool:
    """Callable wrapper that self-heals on failure.

    Behaves like a plain function so LangGraph nodes can call it directly:

        healed = heal.wrap(weather_v1, fallback=weather_v2,
                            recipe=TransformRecipe(field_map={'city': 'location'}))
        result = healed({"city": "Bengaluru"}, config=config)
    """

    def __init__(self, primary: "BaseTool", *,
                 fallback: "BaseTool" | None = None,
                 recipe: TransformRecipe | None = None,
                 on_heal: Callable[[dict[str, Any], dict[str, Any]], None] | None = None) -> None:
        if fallback is None and recipe is None:
            raise ValueError(
                "heal.wrap requires at least one of `fallback` or `recipe`. "
                "Nothing to heal to otherwise."
            )
        self.primary = primary
        self.fallback = fallback
        self.recipe = recipe
        self.on_heal = on_heal

    @property
    def name(self) -> str:
        return self.primary.name

    def __call__(self, args: dict[str, Any],
                 config: "RunnableConfig" | None = None) -> Any:
        try:
            return self.primary.invoke(args, config=config)
        except Exception as primary_err:
            healed_args = self.recipe.apply(args) if self.recipe else dict(args)
            target = self.fallback or self.primary
            if self.on_heal is not None:
                try:
                    self.on_heal(args, healed_args)
                except Exception:
                    pass
            try:
                return target.invoke(healed_args, config=config)
            except Exception:
                # Re-raise the ORIGINAL error — the primary is the tool the
                # user actually asked for; the fallback was a rescue attempt.
                raise primary_err


def wrap(primary: "BaseTool", *,
         fallback: "BaseTool" | None = None,
         recipe: TransformRecipe | None = None,
         on_heal: Callable[[dict[str, Any], dict[str, Any]], None] | None = None) -> HealedTool:
    """Wrap a tool so failures re-route through `fallback` (with `recipe`)."""
    return HealedTool(primary, fallback=fallback, recipe=recipe, on_heal=on_heal)


def route(tools_by_name: dict[str, "BaseTool"],
          name: str,
          args: dict[str, Any],
          *,
          rulebook: "Rulebook | None" = None,
          intent: str | None = None,
          config: "RunnableConfig | None" = None) -> Any:
    """Dispatch `name(args)` — but consult `rulebook` first.

    If the rulebook holds an approved substitution for `(intent, name)`, the
    approved `with_skill` is invoked with args translated by the rule's
    recipe. No retry, no failure telemetry — a promoted rule is the runtime's
    new default. This is the surface that closes the intelligence loop:

        mine (offline) -> promote (human) -> route (runtime, zero LLM)

    Falls back to invoking `name` directly if no rule matches or the target
    skill is missing from `tools_by_name`.
    """
    if rulebook is not None:
        rule = rulebook.find_substitution(name, intent=intent)
        if rule is not None and rule.with_skill in tools_by_name:
            recipe = TransformRecipe.from_dict(rule.recipe)
            translated = recipe.apply(args)
            return tools_by_name[rule.with_skill].invoke(translated, config=config)

    return tools_by_name[name].invoke(args, config=config)
