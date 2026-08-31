"""Regression: a promoted tool_binding rule must dispatch to the tool it
NAMES (with_skill), not to whatever fallback the drifted tool happens to
have configured.

External probe (2026-08-30): rung 2 always called the locally configured
fallback, executing the rule's recipe against a target the rule never
cited. Now: the interceptor resolves ``with_skill`` through the session's
tool registry; unresolvable targets degrade to the fallback LOUDLY.
"""
from __future__ import annotations

import graxella
from graxella.gate.spec import ArtifactKind, Proposal, TargetScope


def _promote_binding(grx, *, replace: str, with_skill: str) -> None:
    payload = {"replace_skill": replace, "with_skill": with_skill,
               "field_map": {"city": "location"}}
    target = TargetScope(domain="weather", tool=replace)
    p = Proposal(id=Proposal.deterministic_id(ArtifactKind.TOOL_BINDING,
                                              target, payload),
                 kind=ArtifactKind.TOOL_BINDING, target=target,
                 payload=payload, origin="test")
    grx.rulebook.promote(p, approved_by="operator:test")


def _drifting_weather(grx, fallback):
    @grx.tool(name="get_weather", fallback=fallback)
    def get_weather(city: str) -> str:
        """old weather tool"""
        raise TypeError("unexpected keyword argument 'city'; schema "
                        "deprecated - use 'location' instead")
    return grx.tools["get_weather"]


def test_rung2_dispatches_to_named_substitute():
    grx = graxella.Session("t", domain="weather", workdir="ephemeral")
    calls = []

    @grx.tool
    def fetch_forecast(location: str) -> str:
        """new weather tool"""
        calls.append(location)
        return f"forecast {location}"

    def wrong_fallback(args):
        raise AssertionError("fallback must not run when with_skill resolves")

    old = _drifting_weather(grx, wrong_fallback)
    _promote_binding(grx, replace="get_weather", with_skill="fetch_forecast")

    assert old.invoke({"city": "Paris"}) == "forecast Paris"
    assert calls == ["Paris"]        # recipe applied, NAMED target executed


def test_rung2_unresolvable_substitute_degrades_loudly(caplog):
    grx = graxella.Session("t2", domain="weather", workdir="ephemeral")
    hits = []

    def fallback(args):
        hits.append(args)
        return "fallback served"

    old = _drifting_weather(grx, fallback)
    _promote_binding(grx, replace="get_weather", with_skill="not_registered")

    import logging
    with caplog.at_level(logging.WARNING, logger="graxella"):
        assert old.invoke({"city": "Paris"}) == "fallback served"
    assert hits == [{"location": "Paris"}]
    assert any("not_registered" in r.message for r in caplog.records), \
        "degradation must be loud"
