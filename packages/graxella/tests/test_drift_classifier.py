"""The widened drift classifier -- the external probe set, both directions.

External criticism (2026-08-30): drift detection was one regex, so a 404
endpoint removal, a pydantic/jsonschema ValidationError, or a schema
change never reached the heal ladder. classify_drift widens detection to
typed signals -- while the false-positive guards below keep auth errors,
timeouts, and plain record-miss 404s as ordinary loud failures.
"""
from __future__ import annotations

import graxella
from graxella.healing import classify_drift


# ------------------------------------------------ real library exceptions

def _pydantic_error():
    from pydantic import BaseModel

    class ShipmentQuery(BaseModel):
        order_ref: str

    try:
        ShipmentQuery(order_id="1234")  # old-schema args vs new model
    except Exception as exc:
        return exc
    raise AssertionError("expected a validation error")


def _jsonschema_error():
    import jsonschema
    try:
        jsonschema.validate({"order_id": "1"},
                            {"type": "object",
                             "required": ["order_ref"],
                             "additionalProperties": False})
    except Exception as exc:
        return exc
    raise AssertionError("expected a validation error")


def test_validation_errors_are_drift():
    assert classify_drift(_pydantic_error()) is not None
    assert classify_drift(_jsonschema_error()) is not None


def test_http_gone_is_drift():
    import urllib.error
    e410 = urllib.error.HTTPError("http://api/x", 410, "Gone", {}, None)
    assert classify_drift(e410) == "http_gone"

    class _Resp:
        status_code = 404

    class CarrierError(Exception):
        response = _Resp()

    assert classify_drift(
        CarrierError("endpoint /api/v1/track no longer exists")) == "http_gone"


def test_signature_shapes_are_drift():
    assert classify_drift(
        TypeError("unexpected keyword argument 'city'")) == "signature"
    assert classify_drift(
        TypeError("missing 1 required positional argument: "
                  "'tracking_ref'")) == "signature"


# ------------------------------------------------ false-positive guards

def test_ordinary_failures_are_not_drift():
    assert classify_drift(ValueError("upstream returned garbage")) is None
    assert classify_drift(TimeoutError("read timed out")) is None
    assert classify_drift(
        PermissionError("401 unauthorized: bad api key")) is None
    assert classify_drift(KeyError("temp_c")) is None
    # a record-miss 404 is a normal miss, not an API migration
    assert classify_drift(Exception("404: order ORD-9 not found")) is None


# ------------------------------------------------ deterministic repair

def test_deterministic_recipe_from_real_pydantic_error():
    """The unambiguous rename (one missing field, one source arg) is
    derived with ZERO model involvement -- from pydantic's real message."""
    from graxella.healing.dspy_healer import _deterministic_recipe
    err = str(_pydantic_error())
    r = _deterministic_recipe({"order_id": "1234"}, err)
    assert r is not None and r.field_map == {"order_id": "order_ref"}

    err_js = str(_jsonschema_error())
    r2 = _deterministic_recipe({"order_id": "1"}, err_js)
    assert r2 is not None and r2.field_map == {"order_id": "order_ref"}

    # classic TypeError shape: rejected field named explicitly
    r3 = _deterministic_recipe(
        {"city": "Paris", "units": "metric"},
        "unexpected keyword argument 'city'; 'location' is a required "
        "property")
    assert r3 is not None and r3.field_map == {"city": "location"}


def test_deterministic_recipe_refuses_ambiguity():
    from graxella.healing.dspy_healer import _deterministic_recipe
    err = str(_pydantic_error())          # one missing field...
    # ...but TWO candidate sources -> ambiguous -> None (LLM's job)
    assert _deterministic_recipe({"a": 1, "b": 2}, err) is None
    # no missing field named at all -> None
    assert _deterministic_recipe({"a": 1}, "boom") is None


# ------------------------------------------------ end to end: real error

def test_pydantic_drift_reaches_heal_ladder(monkeypatch):
    """A tool that raises a GENUINE pydantic ValidationError (no crafted
    string anywhere) is healed -- the de-curated path the critic asked
    for."""
    def fake_build_default_healer(model_id=None, **_):
        def healer(tool_name, args, error):
            # the real pydantic message names the new field
            assert "order_ref" in error
            return graxella.TransformRecipe(
                field_map={"order_id": "order_ref"})
        return healer
    monkeypatch.setattr("graxella.healing.dspy_healer.build_default_healer",
                        fake_build_default_healer)

    grx = graxella.Session("t", domain="ship", workdir="ephemeral")

    from pydantic import BaseModel

    class ShipmentQuery(BaseModel):        # the carrier's NEW schema
        order_ref: str

    def carrier_v2(args: dict) -> str:
        q = ShipmentQuery(**args)
        return f"in transit ({q.order_ref})"

    @grx.tool(name="track", fallback=carrier_v2)
    def track(order_id: str) -> str:
        """track a shipment"""
        return carrier_v2({"order_id": order_id})   # genuine failure

    assert track.invoke({"order_id": "1234"}) == "in transit (1234)"
    assert grx.healer_calls == 1
    assert len(grx.pending()) == 1
