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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig  # noqa: F401
    from langchain_core.tools import BaseTool  # noqa: F401

    from graxella.rulebook import Rulebook  # noqa: F401


@dataclass
class TransformRecipe:
    """Deterministic source→target arg mapping. Applied on failure.

    field_map        — {"city": "location"} renames the source field.
    static_defaults  — {"units": "metric"} fills missing target fields.
    drop_fields      — ("legacy_flag",) removes fields the target rejects.
    """
    field_map: dict[str, str] = field(default_factory=dict)
    static_defaults: dict[str, Any] = field(default_factory=dict)
    drop_fields: tuple[str, ...] = ()

    def apply(self, args: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in args.items():
            if k in self.drop_fields:
                continue
            out[self.field_map.get(k, k)] = v
        for k, v in self.static_defaults.items():
            out.setdefault(k, v)
        return out


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
            recipe = TransformRecipe(
                field_map=dict(rule.recipe.get("field_map", {})),
                static_defaults=dict(rule.recipe.get("static_defaults", {})),
                drop_fields=tuple(rule.recipe.get("drop_fields", ())),
            )
            translated = recipe.apply(args)
            return tools_by_name[rule.with_skill].invoke(translated, config=config)

    return tools_by_name[name].invoke(args, config=config)
