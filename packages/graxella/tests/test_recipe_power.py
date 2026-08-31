"""TransformRecipe power: dotted-path restructuring, type casts, value
maps. External criticism (2026-08-30): "A TransformRecipe can rename flat
string fields, add static defaults, drop fields. It cannot express nested
restructuring, type coercion, value mapping." This closes that gap —
still zero-dep, deterministic, and total (a bad cast is skipped, never
crashes a heal).
"""
from __future__ import annotations

import graxella
from graxella.healing.recipes import TransformRecipe


# --------------------------------------------------------- flat (unchanged)

def test_flat_recipe_behaves_exactly_as_before():
    r = TransformRecipe(field_map={"city": "location"},
                        static_defaults={"units": "metric"},
                        drop_fields=("legacy",))
    assert r.apply({"city": "Paris", "legacy": 1}) == \
        {"location": "Paris", "units": "metric"}


# ------------------------------------------------------------ dotted paths

def test_nested_restructuring():
    r = TransformRecipe(field_map={"user.email": "contact.email_address"})
    out = r.apply({"user": {"email": "a@b.com"}, "other": 1})
    assert out == {"user": {}, "contact": {"email_address": "a@b.com"},
                   "other": 1}


def test_flatten_to_nested_and_back():
    r1 = TransformRecipe(field_map={"city": "address.city"})
    assert r1.apply({"city": "Paris"}) == {"address": {"city": "Paris"}}
    r2 = TransformRecipe(field_map={"address.city": "city"})
    assert r2.apply({"address": {"city": "Paris"}}) == {"address": {},
                                                         "city": "Paris"}


def test_dotted_static_default_and_type_cast():
    r = TransformRecipe(static_defaults={"meta.units": "metric"},
                        type_casts={"age": "int"})
    out = r.apply({"age": "34"})
    assert out == {"age": 34, "meta": {"units": "metric"}}


# --------------------------------------------------------------- type casts

def test_type_casts_all_kinds():
    r = TransformRecipe(type_casts={"n": "int", "f": "float",
                                    "s": "str", "b": "bool"})
    out = r.apply({"n": "3", "f": "2.5", "s": 9, "b": "yes"})
    assert out == {"n": 3, "f": 2.5, "s": "9", "b": True}


def test_bad_cast_is_skipped_never_raised():
    r = TransformRecipe(type_casts={"n": "int"})
    out = r.apply({"n": "not-a-number"})
    assert out["n"] == "not-a-number"     # unchanged, no crash


# --------------------------------------------------------------- value map

def test_value_map_remaps_known_values_only():
    r = TransformRecipe(value_map={"status": {"active": "ACTIVE",
                                              "inactive": "INACTIVE"}})
    assert r.apply({"status": "active"})["status"] == "ACTIVE"
    assert r.apply({"status": "unknown"})["status"] == "unknown"  # passthrough


def test_apply_order_rename_then_valuemap_then_cast_then_default():
    r = TransformRecipe(
        field_map={"stat": "status"},
        value_map={"status": {"1": "on"}},
        type_casts={"status": "str"},
        static_defaults={"status": "off"},
    )
    assert r.apply({"stat": "1"})["status"] == "on"
    assert r.apply({})["status"] == "off"          # default fills when absent


# ------------------------------------------------------- round-trip / dict

def test_to_dict_from_dict_round_trip():
    r = TransformRecipe(field_map={"a.b": "c.d"}, static_defaults={"x": 1},
                        drop_fields=("y",), type_casts={"n": "int"},
                        value_map={"s": {"a": "b"}})
    r2 = TransformRecipe.from_dict(r.to_dict())
    assert r2 == r


def test_from_dict_backward_compatible_with_old_three_field_shape():
    """A rulebook file written before this change (no type_casts/value_map
    keys) must still load and apply correctly."""
    old = {"field_map": {"city": "location"}, "static_defaults": {},
          "drop_fields": []}
    r = TransformRecipe.from_dict(old)
    assert r.apply({"city": "Paris"}) == {"location": "Paris"}


# ------------------------------------------------------ end-to-end: a heal
# that needs restructuring + a cast + a value remap in one recipe.

def test_end_to_end_heal_with_nested_cast_and_remap(monkeypatch):
    def fake_build_default_healer(model_id=None, **_):
        def healer(tool_name, args, error):
            return TransformRecipe(
                field_map={"customer_id": "customer.id",
                          "is_active": "customer.status"},
                type_casts={"customer.id": "int"},
                value_map={"customer.status": {True: "ACTIVE",
                                               False: "INACTIVE"}})
        return healer
    monkeypatch.setattr("graxella.healing.dspy_healer.build_default_healer",
                        fake_build_default_healer)

    grx = graxella.Session("t", domain="crm", workdir="ephemeral")

    def v2(args: dict) -> str:
        c = args["customer"]
        assert isinstance(c["id"], int) and c["status"] in ("ACTIVE", "INACTIVE")
        return f"customer {c['id']} is {c['status']}"

    @grx.tool(name="lookup", fallback=v2)
    def lookup(customer_id: str, is_active: bool) -> str:
        """lookup a customer"""
        raise TypeError("unexpected keyword argument 'customer_id'; schema "
                        "deprecated - use 'customer.id' instead")

    out = lookup.invoke({"customer_id": "42", "is_active": True})
    assert out == "customer 42 is ACTIVE"
