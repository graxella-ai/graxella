"""Tasks 2-4/2-5 — the heal ladder: fail once, learn forever."""
from __future__ import annotations

import pytest

from axon_fabric.interceptor import ToolInterceptor, is_drift
from graxella.beliefs import Memory
from graxella.gate.evidence import EvidenceGate, pending_from_ledger
from graxella.healing import TransformRecipe
from graxella.rulebook import Rulebook


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "m.db"), agent_id="fab",
                         namespace="weather")


def weather_v1(args):
    raise RuntimeError("HTTP_410_GONE: schema deprecated, use weather.v2")


def weather_v2(args):
    if "location" not in args:
        raise RuntimeError("unknown field: expected 'location'")
    return f"sunny in {args['location']}"


def make(memory, tmp_path, **kw):
    return ToolInterceptor(
        weather_v1, tool_name="get_weather", memory=memory,
        rulebook=Rulebook(path=tmp_path / "rb.json"),
        fallback=weather_v2, domain="weather", model_id="stub", **kw)


def test_happy_path_records_tool_outcome(memory, tmp_path):
    it = ToolInterceptor(lambda a: "ok", tool_name="t", memory=memory,
                         rulebook=Rulebook(path=tmp_path / "rb.json"),
                         domain="weather")
    assert it({"x": 1}) == "ok"
    rows = memory.beliefs(predicate="outcome")
    assert len(rows) == 1 and rows[0]["object"] == "ok"


def test_promoted_transform_heals_with_zero_llm(memory, tmp_path):
    it = make(memory, tmp_path)
    it.rulebook.promote(
        TransformRecipe(field_map={"city": "location"}).to_proposal(
            domain="weather", tool="get_weather", origin="healer:test"),
        approved_by="sridhar", domain="weather")
    assert it({"city": "Bengaluru"}) == "sunny in Bengaluru"
    assert it.healer_calls == 0                      # rung 2: no LLM, ever
    # The heal recorded under the transform tuple — warming the gate.
    gate = EvidenceGate(memory)
    from graxella.gate.spec import ArtifactKind, TargetScope
    prior = gate.prior(ArtifactKind.TRANSFORM,
                       TargetScope(domain="weather", tool="get_weather",
                                   model_id="stub"))
    assert prior.successes == 1


def test_heal_once_proposes_through_the_gate(memory, tmp_path):
    calls = {"n": 0}

    def healer(tool, args, error):
        calls["n"] += 1
        return TransformRecipe(field_map={"city": "location"})

    it = make(memory, tmp_path, healer=healer)
    assert it({"city": "Delhi"}) == "sunny in Delhi"     # healed live
    assert calls["n"] == 1
    # The recipe became a cold proposal in the human review queue,
    # carrying its live paired-replay citation.
    pend = pending_from_ledger(memory)
    assert len(pend) == 1 and pend[0]["kind"] == "transform"
    assert memory.beliefs(predicate="paired_replay")


def test_fail_once_learn_forever(memory, tmp_path):
    """The signature demo: heal once via LLM, promote, and the second
    drift costs zero healer calls."""
    calls = {"n": 0}

    def healer(tool, args, error):
        calls["n"] += 1
        return TransformRecipe(field_map={"city": "location"})

    it = make(memory, tmp_path, healer=healer)
    it({"city": "Delhi"})                                # rung 3: LLM once
    pid = pending_from_ledger(memory)[0]["proposal_id"]
    gate = EvidenceGate(memory)
    gate.approve(pid, by="sridhar", note="field rename verified")
    _, approved = gate.decide(
        TransformRecipe(field_map={"city": "location"}).to_proposal(
            domain="weather", tool="get_weather", origin="healer:axon-fabric",
            model_id="stub"))
    it.rulebook.promote(approved, domain="weather")

    assert it({"city": "Chennai"}) == "sunny in Chennai"  # rung 2 now
    assert calls["n"] == 1                                # NO second LLM call


def test_non_drift_errors_pass_through(memory, tmp_path):
    def broken(args):
        raise ValueError("plain bug, not drift")

    it = ToolInterceptor(broken, tool_name="t", memory=memory,
                         rulebook=Rulebook(path=tmp_path / "rb.json"),
                         fallback=weather_v2, domain="weather")
    with pytest.raises(ValueError):
        it({"x": 1})
    assert memory.beliefs(predicate="outcome")[0]["object"] == "fail"


def test_drift_with_no_ladder_fails_loudly(memory, tmp_path):
    it = ToolInterceptor(weather_v1, tool_name="get_weather", memory=memory,
                         rulebook=Rulebook(path=tmp_path / "rb.json"),
                         domain="weather")
    with pytest.raises(RuntimeError, match="no fallback"):
        it({"city": "x"})


def test_drift_signature():
    assert is_drift("HTTP_410_GONE: moved")
    assert is_drift("unexpected keyword argument 'city'")
    assert not is_drift("connection timed out")
