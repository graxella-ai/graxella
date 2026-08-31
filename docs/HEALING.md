# What graxella's self-healing actually does

An honest capability table. If a reader can predict heal-vs-no-heal
before running graxella, this document did its job.

## The heal ladder

Every governed tool call (`@grx.tool(fallback=...)`) runs through four
rungs, cheapest first:

1. **happy path** — the primary call succeeds. Nothing else runs.
2. **promoted heal** — the rulebook holds an ACTIVE, gate-approved
   `TransformRecipe` for this tool: apply it, call the target
   deterministically. Zero LLM.
3. **heal-once** — no promoted rule yet: the healer proposes a
   `TransformRecipe` **once**. If it's a genuinely unambiguous rename
   (see below), the recipe is derived deterministically, with **zero
   model calls**. Otherwise the healer's LLM engine (DSPy, or direct
   Ollama JSON mode) proposes it. Either way the recipe is cached,
   applied, and shipped to the Evidence Gate for review — the model
   never re-fires for this exact drift again.
4. **loud failure** — nothing worked: a typed failure outcome is
   recorded and the original error propagates. graxella never retries
   silently and never fakes a heal.

A rule that starts failing in production doesn't stay active forever
either: `Session.reconcile()` also **demotes** a promoted rule whose
recent operational record turns bad (see `graxella.gate.health`) — the
rulebook is evidence-gated in both directions, not a write-once list.

## What counts as drift (and what deliberately doesn't)

`graxella.healing.classify_drift` — pluggable via
`Session(drift_classifier=...)` — recognizes three families:

| Class | Signal | Example |
|---|---|---|
| `signature` | classic explicit markers, plus missing/unexpected-argument shapes | `unexpected keyword argument 'city'`, `missing 1 required positional argument` |
| `validation` | a validation library rejected the args, by exception type name (pydantic / jsonschema / marshmallow — duck-typed, no import needed) | `pydantic.ValidationError`, `jsonschema.exceptions.ValidationError` |
| `http_gone` | HTTP 410, or a 404 whose text says the *endpoint* moved | `410 Gone`, `"endpoint /api/v1/track no longer exists"` |

**Deliberately NOT drift** — these stay ordinary, loud, un-healed
failures, on purpose:

- auth failures (`401`, `403`, bad API key)
- timeouts
- `KeyError` / generic `ValueError` with no schema signal
- a plain **record-miss** 404 (`"order ORD-9 not found"`) — that's a
  normal miss, not an API migration. Only a 404 whose message names an
  endpoint/route/path is treated as drift.

If your deployment needs a wider or narrower net, pass your own
`drift_classifier: Callable[[BaseException], str | None]`.

## What a repair recipe can express

`TransformRecipe` operates on dotted paths (`"user.email"`), so a plain
key (`"city"`) is just a one-segment path — nothing here changes
existing flat-recipe behavior.

| Capability | Field | Example |
|---|---|---|
| Rename / move a field | `field_map` | `{"city": "location"}` |
| Nested restructuring | `field_map` (dotted) | `{"customer_id": "customer.id"}` |
| Fill a missing field | `static_defaults` | `{"units": "metric"}` |
| Drop a rejected field | `drop_fields` | `("legacy_flag",)` |
| Coerce a type | `type_casts` | `{"age": "int"}` — one of `int`/`float`/`str`/`bool` |
| Remap specific values | `value_map` | `{"status": {"active": "ACTIVE"}}` |

Apply order: drop → rename → value-map → type-cast → fill defaults.

**Total, not partial**: a bad cast (`int("abc")`) is *skipped*, never
raised — a repair must never trade a recoverable drift for a crash. A
`static_defaults`/`field_map` write into a path that collides with an
existing scalar coerces that scalar into a dict rather than raising.

**What it still can't express** (honestly, as of this writing): calling
a different HTTP method, rotating auth, or a response-shape transform on
the *return value* (recipes transform call arguments, not results). A
JSONata-style transform engine for response-shape drift is a real B10
lineage feature that hasn't landed in this runtime — if you need it,
write a custom `fallback` that does the response transform itself; the
recipe still handles the request-side rename.

## The deterministic-repair shortcut

Before any model is consulted, `dspy_healer._deterministic_recipe` checks
whether the drift error names **exactly one missing field** with
**exactly one plausible source argument** to donate its value — the
overwhelmingly common case (a field got renamed). If so, the rename is
derived by regex over the validator's own message, with **zero model
calls**, and works even when no LLM engine is installed at all. Ambiguous
drift (multiple missing fields, multiple candidate sources, a genuine
restructuring) falls through to the LLM engine.

## Where to look

- `graxella/healing/interceptor.py` — the heal ladder, `classify_drift`
- `graxella/healing/recipes.py` — `TransformRecipe`
- `graxella/healing/dspy_healer.py` — the built-in healer engine +
  deterministic pre-pass
- `graxella/gate/health.py` — demotion (un-learning)
- `tutorials/02_self_healing.py`, `tutorials/07_langchain_agent.py` —
  runnable proof, healing a **genuine** validation error (no crafted
  strings)
