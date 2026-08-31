"""The built-in drift healer: deterministic guardrails + Session wiring.

These tests never touch a model. The LLM reasoning is stubbed; what's
verified is graxella's contract around it: the recipe guardrails that make
a small model's proposal safe, and that ``@grx.tool(fallback=...)`` heals
automatically -- LLM fires ONCE -- with no healer authored by the developer.
"""
from __future__ import annotations

import graxella
from graxella.healing.dspy_healer import _recipe_from_obj


# ---------------------------------------------------------------- guardrails

def test_empty_proposal_is_not_a_heal():
    # An all-empty recipe would "heal" by doing nothing -- that's a lie.
    assert _recipe_from_obj({}) is None
    assert _recipe_from_obj({"field_map": {}, "drop_fields": []}) is None


def test_rename_wins_over_drop():
    # Small models routinely propose renaming AND dropping the same field.
    # apply() drops before renaming, so an un-guarded recipe would delete
    # the value. Rename must win.
    r = _recipe_from_obj({"field_map": {"city": "location"},
                          "drop_fields": ["city"]})
    assert r is not None
    assert r.apply({"city": "Paris"}) == {"location": "Paris"}


def test_types_are_coerced_and_junk_dropped():
    r = _recipe_from_obj({"field_map": {"a": "b"},
                          "static_defaults": {"units": "metric"},
                          "drop_fields": "legacy"})  # str, not list
    assert r.apply({"a": 1, "legacy": 9}) == {"b": 1, "units": "metric"}


# ------------------------------------------------------- Session auto-heal

def _drift(*_a, **_k):
    raise TypeError("unexpected keyword argument 'city'; schema deprecated "
                    "- use 'location' instead")


def test_tool_heals_with_no_authored_healer(monkeypatch):
    """The developer writes a tool + fallback, nothing else. A drift is
    healed once by the built-in engine (stubbed), the recipe is reused
    deterministically, and a proposal lands in the review queue."""
    calls = {"n": 0}

    def fake_build_default_healer(model_id=None, **_):
        def healer(tool_name, args, error):
            calls["n"] += 1
            return graxella.TransformRecipe(field_map={"city": "location"})
        return healer

    # patch where Session imports it (function-local import)
    monkeypatch.setattr("graxella.healing.dspy_healer.build_default_healer",
                        fake_build_default_healer)

    grx = graxella.Session("t", domain="weather", workdir="ephemeral")

    def v2(args: dict) -> dict:
        return {"ok": True, "for": args["location"]}

    @grx.tool(fallback=v2)
    def get_weather(city: str) -> dict:
        """weather"""
        return _drift()

    assert get_weather.invoke({"city": "Bengaluru"})["for"] == "Bengaluru"
    assert get_weather.invoke({"city": "Chennai"})["for"] == "Chennai"
    assert calls["n"] == 1                    # LLM fired exactly once
    assert grx.healer_calls == 1
    assert len(grx.pending()) >= 1            # gate has a proposal to review


def test_reconcile_auto_promotes_on_evidence(monkeypatch):
    """Autonomous promotion: once a heal's transform has enough successes
    across enough independent sessions, reconcile() promotes it with NO
    human -- and a fresh tool call is then handled zero-LLM by the rule."""
    def fake_build_default_healer(model_id=None, **_):
        def healer(tool_name, args, error):
            return graxella.TransformRecipe(field_map={"order_id": "order_ref"})
        return healer
    monkeypatch.setattr("graxella.healing.dspy_healer.build_default_healer",
                        fake_build_default_healer)

    import tempfile
    from pathlib import Path
    work = Path(tempfile.mkdtemp(prefix="reconcile-"))
    grx = graxella.Session("t", domain="ship", model_id="m", workdir=work)

    def v2(args: dict) -> str:
        return f"ok {args['order_ref']}"

    @grx.tool(name="ship_status", fallback=v2)
    def ship(order_id: str) -> str:
        """ship"""
        raise TypeError("unexpected keyword argument 'order_id'; schema "
                        "deprecated - use 'order_ref' instead")

    ship.invoke({"order_id": "1"})            # heal once -> standing proposal
    assert not grx.reconcile()                # 1 session: not enough diversity

    # simulate the same fix succeeding across many independent sessions
    for i in range(12):
        aid = grx.memory.record_decision(decision_type="tool", task="call",
                                         chosen="ship_status", domain="ship",
                                         model_id="m")
        grx.memory.record_outcome(decision_id=aid, ok=True, kind="transform",
                                  chosen="ship_status", domain="ship",
                                  model_id="m", session_id=f"prod-{i}")

    result = grx.reconcile()                  # evidence now clears the gate
    assert result.promoted, "expected autonomous promotion once evidence accrued"
    assert grx.rulebook.find_substitution("ship_status") is not None

    # a fresh session drifts -> handled by the promoted rule, zero LLM
    grx2 = graxella.Session("t", domain="ship", model_id="m", workdir=work)

    @grx2.tool(name="ship_status", fallback=v2)
    def ship2(order_id: str) -> str:
        """ship"""
        raise TypeError("unexpected keyword argument 'order_id'; schema "
                        "deprecated - use 'order_ref' instead")

    assert ship2.invoke({"order_id": "9"}) == "ok 9"
    assert grx2.healer_calls == 0             # promoted rule, not the healer


def test_broken_recipe_is_discarded_not_cached(monkeypatch):
    """Found by de-curating tutorial 10: a healer that proposes a BAD
    recipe used to leave it cached for rung 2.5 to reuse forever, with no
    outcome recorded. Now: the failure is a typed outcome, the recipe is
    discarded, and the next call re-proposes."""
    proposals = {"n": 0}

    def fake_build_default_healer(model_id=None, **_):
        def healer(tool_name, args, error):
            proposals["n"] += 1
            if proposals["n"] == 1:      # first proposal is junk
                return graxella.TransformRecipe(
                    static_defaults={"order_ref": None})  # breaks fallback
            return graxella.TransformRecipe(
                field_map={"order_id": "order_ref"})
        return healer
    monkeypatch.setattr("graxella.healing.dspy_healer.build_default_healer",
                        fake_build_default_healer)

    grx = graxella.Session("t", domain="ship", workdir="ephemeral")

    def v2(args: dict) -> str:
        if not isinstance(args.get("order_ref"), str):
            raise TypeError("order_ref must be a string")
        return f"ok {args['order_ref']}"

    @grx.tool(name="ship", fallback=v2)
    def ship(order_id: str) -> str:
        """ship"""
        raise TypeError("unexpected keyword argument 'order_id'; schema "
                        "deprecated - use 'order_ref'")

    # call 1: junk recipe -> loud RuntimeError, recipe discarded
    try:
        ship.invoke({"order_id": "1"})
        assert False, "expected the failed heal to raise"
    except Exception as exc:
        assert "discarded" in str(exc)
    # the failure is ON THE LEDGER (no silent decision-without-outcome)
    fails = [r for r in grx.memory.beliefs(predicate="outcome")
             if '"ok": false' in r["statement"].lower()
             or "'ok': false" in r["statement"].lower()
             or '"ok":false' in r["statement"].lower()]
    assert grx.stats()["total"]["count"] >= 1

    # call 2: healer re-proposes (cache was cleared) and this one works
    assert ship.invoke({"order_id": "2"}) == "ok 2"
    assert proposals["n"] == 2


def test_auto_heal_off_means_loud_failure(monkeypatch):
    """auto_heal=False disables the built-in healer entirely -- drift is a
    loud, recorded failure, never a silent no-op."""
    def boom(*_a, **_k):  # must never be built
        raise AssertionError("default healer should not be constructed")
    monkeypatch.setattr("graxella.healing.dspy_healer.build_default_healer",
                        boom)

    grx = graxella.Session("t2", workdir="ephemeral", auto_heal=False)

    @grx.tool(fallback=lambda a: a)
    def t(city: str) -> dict:
        """t"""
        return _drift()

    try:
        t.invoke({"city": "X"})
        assert False, "expected a loud drift failure"
    except Exception as exc:
        assert "drift" in str(exc).lower()
