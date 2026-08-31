"""graxella.healing.dspy_healer -- the default drift healer.

The heal ladder's rung 3 is the ONE place an LLM may appear: when a tool
drifts and no gate-approved transform exists yet, a healer proposes a
``TransformRecipe`` ONCE. That recipe is then applied deterministically and
shipped to the Evidence Gate for review -- the LLM never fires again for
this drift (see ``graxella.healing.interceptor``).

This module ships graxella's *default* healer, so ``@grx.tool(fallback=...)``
heals with no extra code. The reasoning engine is **DSPy** (a Signature +
Predict, optimisable offline against the drift ledger). DSPy is an OPTIONAL
extra: when it -- or its ``litellm`` backend -- can't be imported, the same
structured reasoning runs directly against a local model, so the governance
core never hard-depends on a heavy install. Either way the developer never
imports ``dspy``, never writes a Signature, never picks a model: graxella
owns the reasoner underneath.

  author surface   :  @grx.tool(fallback=v2)          # no healer mentioned
  under the hood   :  drift -> DSPy proposes a recipe ONCE
                       -> recipe applied deterministically + gate-reviewed

Design invariants (all upheld here):
  * compile-time LLM > runtime LLM -- the LLM fires once; its output is a
    cached, gated, deterministic recipe.
  * zero LLM in the *decision* loop -- DSPy only *proposes*; the Evidence
    Gate still decides via Bayesian priors.
  * choice is additive -- an explicit ``@grx.healer`` overrides this default;
    nothing here removes the bring-your-own-healer path.
  * failures are honest -- if no engine is available, this returns ``None``
    and the interceptor falls to a loud, recorded failure. It never fakes a
    heal.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from graxella.healing.recipes import TransformRecipe

_log = logging.getLogger("graxella")

DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_API_BASE = "http://localhost:11434"

#: One-shot instruction shared by both engines, so the DSPy path and the
#: direct-Ollama path reason identically -- only the plumbing differs.
_TASK = (
    "You repair a drifted tool call. A tool's API changed and rejected the "
    "arguments it was given. From the OLD argument names and the error "
    "message, infer the smallest transform that fixes the call:\n"
    "  - field_map: {old_name: new_name} for renamed arguments\n"
    "  - static_defaults: {name: value} for new required arguments\n"
    "  - drop_fields: [name, ...] for arguments the new API rejects\n"
    "Rename in preference to dropping. Only include a key when the error "
    "justifies it. Empty objects/lists are valid."
)


def _extract_json(text: str) -> dict:
    """Tolerantly pull the first JSON object out of an LLM response."""
    if not text:
        return {}
    text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


#: Field names surfaced by validation errors, per library:
#: pydantic v2 ("<name>\n  Field required"), jsonschema ("'x' is a
#: required property"), classic signatures ("unexpected keyword
#: argument 'x'" / "required ... argument: 'x'").
_MISSING_FIELD_PATTERNS = (
    re.compile(r"^(\w+)\s*$\n^\s+Field required", re.MULTILINE),
    re.compile(r"'(\w+)' is a required property"),
    re.compile(r"required (?:keyword-only |positional )?argument:? '(\w+)'"),
)
_REJECTED_FIELD_PATTERNS = (
    re.compile(r"unexpected keyword argument '(\w+)'"),
    re.compile(r"unknown (?:field|argument|parameter) '?(\w+)'?"),
)


def _deterministic_recipe(args: dict, error: str) -> Optional[TransformRecipe]:
    """Zero-LLM repair for the unambiguous case: the error names exactly
    one MISSING required field, and exactly one candidate source argument
    exists to donate its value -- that is a rename, derivable without any
    model. Anything ambiguous (several missing fields, several candidate
    sources) returns None and falls through to the LLM engine.

    Compile-time-over-runtime, applied to healing itself: the LLM is a
    last resort, not the first tool.
    """
    missing: list[str] = []
    for pat in _MISSING_FIELD_PATTERNS:
        missing.extend(m for m in pat.findall(error) if m not in missing)
    if len(missing) != 1:
        return None
    target = missing[0]
    rejected: list[str] = []
    for pat in _REJECTED_FIELD_PATTERNS:
        rejected.extend(m for m in pat.findall(error) if m not in rejected)
    # candidate sources: explicitly-rejected args first, else the sole arg
    sources = [r for r in rejected if r in args] or \
              [k for k in args if k != target]
    if len(sources) != 1:
        return None
    return TransformRecipe(field_map={sources[0]: target})


def _recipe_from_obj(obj: dict) -> Optional[TransformRecipe]:
    """Build a TransformRecipe from a parsed {field_map, static_defaults,
    drop_fields} object. Returns None when nothing actionable was proposed
    -- an empty recipe would 'heal' by doing nothing, which is a lie."""
    field_map = obj.get("field_map") or {}
    static_defaults = obj.get("static_defaults") or {}
    drop_fields = obj.get("drop_fields") or []
    if not isinstance(field_map, dict):
        field_map = {}
    if not isinstance(static_defaults, dict):
        static_defaults = {}
    if isinstance(drop_fields, str):
        drop_fields = [drop_fields]
    if not isinstance(drop_fields, (list, tuple)):
        drop_fields = []
    # keep only string->string renames and string drops; static defaults
    # must be JSON scalars — a model that proposes a dict/list default is
    # hallucinating structure, and a bad default turns the heal itself
    # into a new failure.
    field_map = {str(k): str(v) for k, v in field_map.items() if k and v}
    static_defaults = {str(k): v for k, v in static_defaults.items()
                       if k and isinstance(v, (str, int, float, bool))}
    # Deterministic guardrail: a renamed field must not also be dropped.
    # Small models routinely propose both ({"city":"location"} AND
    # drop ["city"]); since apply() drops before renaming, that would
    # silently delete the value. Rename wins — subtract it from drops.
    renamed = set(field_map)
    drop_fields = tuple(str(d) for d in drop_fields if d and str(d) not in renamed)
    if not field_map and not static_defaults and not drop_fields:
        return None
    return TransformRecipe(field_map=field_map,
                           static_defaults=dict(static_defaults),
                           drop_fields=drop_fields)


# --------------------------------------------------------------------------
# Engine A -- DSPy (preferred). A Signature + Predict; optimisable offline.
# --------------------------------------------------------------------------

def _build_dspy_reasoner(model_id: str, api_base: str):
    """Return a callable (tool, args, error) -> obj, or None if DSPy /
    litellm / the model can't be reached."""
    try:
        import dspy  # noqa: F401
    except Exception:
        return None

    try:
        class RepairDrift(dspy.Signature):  # type: ignore[misc]
            __doc__ = _TASK
            tool_name: str = dspy.InputField()
            failed_args: str = dspy.InputField(
                desc="JSON of the arguments the tool rejected")
            error: str = dspy.InputField(
                desc="the drift error message from the tool")
            field_map: str = dspy.OutputField(
                desc="JSON object: old field name -> new field name")
            static_defaults: str = dspy.OutputField(
                desc="JSON object: new required field -> default value")
            drop_fields: str = dspy.OutputField(
                desc="JSON array of field names the new API rejects")

        lm = dspy.LM(f"ollama_chat/{model_id}", api_base=api_base,
                     api_key="", temperature=0.0, cache=False)
        predict = dspy.Predict(RepairDrift)
    except Exception as exc:  # pragma: no cover - construction guard
        _log.debug("graxella: DSPy healer unavailable (%s)", exc)
        return None

    def reason(tool_name: str, args: dict, error: str) -> dict:
        with dspy.context(lm=lm):
            out = predict(tool_name=tool_name,
                          failed_args=json.dumps(args, default=str),
                          error=error)
        return {
            "field_map": _extract_json(getattr(out, "field_map", "") or ""),
            "static_defaults": _extract_json(
                getattr(out, "static_defaults", "") or ""),
            "drop_fields": (lambda t: (json.loads(t) if t.strip().startswith("[")
                                       else []))(
                getattr(out, "drop_fields", "") or ""),
        }

    _log.info("graxella: default healer engine = DSPy (ollama_chat/%s)",
              model_id)
    return reason


# --------------------------------------------------------------------------
# Engine B -- direct Ollama JSON (fallback). No litellm, no DSPy; uses the
# `ollama` package's forced-JSON mode. Reasons from the same _TASK prompt.
# --------------------------------------------------------------------------

def _build_ollama_reasoner(model_id: str, api_base: str):
    try:
        import ollama  # noqa: F401
    except Exception:
        return None
    try:
        client = ollama.Client(host=api_base)
    except Exception:
        return None

    def reason(tool_name: str, args: dict, error: str) -> dict:
        prompt = (
            f"{_TASK}\n\n"
            f"tool_name: {tool_name}\n"
            f"failed_args: {json.dumps(args, default=str)}\n"
            f"error: {error}\n\n"
            'Respond with ONE JSON object with keys "field_map", '
            '"static_defaults", "drop_fields".'
        )
        resp = client.chat(
            model=model_id, format="json",
            options={"temperature": 0.0},
            messages=[{"role": "user", "content": prompt}])
        content = resp.get("message", {}).get("content", "") \
            if isinstance(resp, dict) else \
            getattr(getattr(resp, "message", None), "content", "")
        return _extract_json(content or "")

    _log.info("graxella: default healer engine = Ollama JSON (%s) "
              "[DSPy not installed]", model_id)
    return reason


# --------------------------------------------------------------------------
# Public: build the default Healer (matches healing.interceptor.Healer)
# --------------------------------------------------------------------------

def build_default_healer(model_id: str | None = None, *,
                         api_base: str = DEFAULT_API_BASE,
                         engine: str = "auto"):
    """Return a ``Healer`` -- ``(tool_name, args, error) -> TransformRecipe |
    None`` -- backed by DSPy when available, else direct Ollama, else None.

    ``engine`` is ``"auto"`` (prefer DSPy), ``"dspy"`` (force, None if
    unavailable) or ``"ollama"`` (force the fallback). Returns ``None`` when
    no engine can be built; the caller then leaves healing as a loud failure
    rather than pretending to heal.
    """
    model = model_id or DEFAULT_MODEL
    reasoner = None
    if engine in ("auto", "dspy"):
        reasoner = _build_dspy_reasoner(model, api_base)
    if reasoner is None and engine in ("auto", "ollama"):
        reasoner = _build_ollama_reasoner(model, api_base)
    if reasoner is None:
        # No model anywhere -- the deterministic pre-pass still works for
        # unambiguous single-field renames; only ambiguous drift then
        # fails loudly.
        _log.warning(
            "graxella: no LLM healer engine available (need DSPy or a "
            "reachable Ollama at %s) -- only unambiguous single-field "
            "drift heals (deterministically); ambiguous drift fails "
            "loudly until an engine, a healer, or a promoted transform "
            "exists", api_base)

        def det_only(tool_name: str, args: dict,
                     error: str) -> Optional[TransformRecipe]:
            det = _deterministic_recipe(dict(args), error)
            if det is not None:
                _log.info("graxella: drift on %s repaired deterministically "
                          "(%s) -- no model call", tool_name, det.field_map)
            return det
        return det_only

    def healer(tool_name: str, args: dict, error: str) -> Optional[TransformRecipe]:
        # Unambiguous single-field renames are derived WITHOUT the model --
        # deterministic, instant, and immune to a small model's guesswork.
        det = _deterministic_recipe(dict(args), error)
        if det is not None:
            _log.info("graxella: drift on %s repaired deterministically "
                      "(%s) -- no model call", tool_name, det.field_map)
            return det
        try:
            obj = reasoner(tool_name, dict(args), error)
        except Exception as exc:
            # The LLM misbehaving must never crash the tool call: a failed
            # proposal drops us to the interceptor's loud-failure rung.
            _log.warning("graxella: default healer errored for %s (%s)",
                         tool_name, exc)
            return None
        recipe = _recipe_from_obj(obj if isinstance(obj, dict) else {})
        if recipe is None:
            _log.info("graxella: default healer proposed no transform for %s",
                      tool_name)
        return recipe

    return healer


__all__ = ["build_default_healer", "DEFAULT_MODEL", "DEFAULT_API_BASE"]
