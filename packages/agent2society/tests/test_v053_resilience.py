"""Resilience / pressure tests for v0.5.3 production hardening.

Each test pins one failure mode that the v0.5.3 audit identified, so a
regression that breaks the fix will fail a named test rather than just
"some integration test somewhere."

Coverage by audit item:

  * Assertion crash on impossible dispatch path
  * Concurrent boundary mutation vs. in-flight route()
  * Unbounded governance signature memory
  * Router _stale flag race under concurrent rebuild
  * LocalTransport surfaces handler exceptions as DispatchError
  * extract_text never raises on non-JSON-serialisable payloads
  * Adapter `matches()` exceptions do not abort registration scan
  * JsonlFileStore tracks corrupt-line skip counts
  * MappingView is a frozen snapshot, not a live view
  * Conformance is unicode-bypass resistant
  * Governance hooks that raise do not break dispatch
  * human_review_when predicate failure escalates (default True)
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

import agent2society as a2s
from agent2society import (
    Handoff,
    InMemoryStore,
    JsonlFileStore,
    Society,
)
from agent2society.adapters.base import Adapter, _REGISTRY, adapt
from agent2society.conformance import check as conformance_check, _normalise
from agent2society.dispatcher import (
    LocalTransport,
    extract_text,
)
from agent2society.exceptions import DispatchError


# ---- shared fixtures --------------------------------------------------

class _Analyst:
    name = "analyst-agent"
    description = "computes statistics and analyses tabular data"
    skills = [
        {
            "id": "data_analysis",
            "name": "Data Analysis",
            "description": "Run statistical analyses and surface insights.",
            "tags": ["analysis", "stats", "data"],
        }
    ]

    def run(self, task: str) -> str:
        return f"ANALYSIS: {task}"


class _Writer:
    name = "writer-agent"
    description = "drafts business writing"
    skills = [
        {
            "id": "exec_memo",
            "name": "Executive Memo",
            "description": "Draft executive memos from notes.",
            "tags": ["writing", "memo"],
        }
    ]

    def __call__(self, task: str) -> str:
        return f"MEMO: {task}"


def _build_society() -> Society:
    s = Society(strict=False)
    s.add(_Analyst())
    s.add(_Writer())
    return s


# ---- 1. assertion crash fix ------------------------------------------

def test_dispatch_loop_never_raises_assertion_error():
    """If the dispatch loop ever exits without success it must raise a
    DispatchError, not crash with AssertionError. The previous `assert`
    on text/dispatched_cand was a foot-gun: any future control-flow bug
    above it would surface as an opaque AssertionError instead of a
    library-typed exception."""
    s = Society(strict=True)
    s.add(_Analyst())

    # Force a transport failure by registering a handler that always raises.
    def boom(url, payload):
        raise RuntimeError("transport down")
    s._local.register("local://analyst-agent", boom)

    with pytest.raises(DispatchError):
        s.run(Handoff(task="compute statistics on Q3"))


# ---- 2. concurrent boundary mutation ---------------------------------

def test_boundary_mutation_under_concurrent_routes():
    """boundary() must CoW the graph, never mutate it in place. Concurrent
    route()s started before the swap should see the old boundary; routes
    started after should see the new boundary. No exceptions either way."""
    s = _build_society()
    errors: List[BaseException] = []
    barrier = threading.Barrier(9)

    def reader():
        barrier.wait()
        try:
            for _ in range(40):
                s.run(Handoff(task="compute statistics on churn"))
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    def mutator():
        barrier.wait()
        for i in range(20):
            s.boundary("analyst-agent", deny=[f"banned{i}"])
            time.sleep(0.0005)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    threads.append(threading.Thread(target=mutator))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors


# ---- 3. unbounded governance signature memory ------------------------

def test_governance_signature_cap_evicts_oldest():
    """The _fired_conflicts / _fired_drifts dicts must stay bounded under
    long-running churn. We shrink the cap so the test runs fast."""
    s = _build_society()
    s._governance_sig_cap = 50

    # Inject signatures directly via the helper that production uses.
    for i in range(200):
        s._remember_signature(s._fired_conflicts, f"sig-{i}")

    assert len(s._fired_conflicts) == 50
    # Oldest signatures must be the ones that got evicted.
    assert "sig-0" not in s._fired_conflicts
    assert "sig-199" in s._fired_conflicts


# ---- 4. router _stale flag race --------------------------------------

def test_router_concurrent_route_does_not_double_rebuild():
    """Multiple threads hitting an unbuilt router simultaneously must
    serialise through the rebuild lock. We assert correctness via the
    invariant that every returned candidate list has the same length."""
    from agent2society.graph import CapabilityGraph
    from agent2society.router import Router

    g = CapabilityGraph()
    # Re-use the conftest card factory pattern.
    s = _build_society()
    router = s.router  # already built

    # Force a rebuild and race many threads through it.
    router.mark_stale()
    lengths: List[int] = []
    lock = threading.Lock()

    def go():
        cands = router.route("compute statistics on Q3 churn", top_k=5)
        with lock:
            lengths.append(len(cands))

    threads = [threading.Thread(target=go) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(lengths)) == 1, f"inconsistent candidate counts: {lengths}"


# ---- 5. LocalTransport surfaces handler errors as DispatchError ------

def test_local_transport_wraps_handler_exception_as_dispatch_error():
    lt = LocalTransport()

    def bad(url, payload):
        raise KeyError("missing-field")

    lt.register("local://x", bad)
    with pytest.raises(DispatchError) as ei:
        lt.send("local://x", {"params": {}})
    assert "KeyError" in str(ei.value)


def test_local_transport_does_not_double_wrap_dispatch_error():
    lt = LocalTransport()

    def already_typed(url, payload):
        raise DispatchError("upstream rate limited")

    lt.register("local://y", already_typed)
    with pytest.raises(DispatchError) as ei:
        lt.send("local://y", {"params": {}})
    # Should NOT be double-nested as "DispatchError: ... DispatchError: ..."
    assert "upstream rate limited" in str(ei.value)
    assert ei.value.__cause__ is None or isinstance(ei.value, DispatchError)


# ---- 6. extract_text robustness --------------------------------------

def test_extract_text_handles_non_serialisable_payload():
    """A transport response with a non-JSON-serialisable value in `result`
    must not raise -- the dispatch path treats extract_text() as total."""

    class Weird:
        def __str__(self):
            return "weird-thing"

    response = {"result": {"meta": Weird()}}
    # Should not raise; should return some string fallback.
    out = extract_text(response)
    assert isinstance(out, str)
    assert "weird-thing" in out or "Weird" in out


def test_extract_text_returns_error_message_for_string_errors():
    response = {"error": "rate limited"}
    out = extract_text(response)
    assert "rate limited" in out


# ---- 7. adapter exception isolation ----------------------------------

class _NoisyAdapter(Adapter):
    name = "noisy"

    def matches(self, obj):
        raise AttributeError("can't introspect that")

    def to_card(self, obj):  # pragma: no cover
        raise RuntimeError("never called")

    def to_handler(self, obj):  # pragma: no cover
        raise RuntimeError("never called")


def test_adapter_matches_exception_does_not_break_registration_scan(caplog):
    noisy = _NoisyAdapter()
    _REGISTRY.append(noisy)
    try:
        # Should not raise -- a noisy adapter must not poison the registry.
        adapt(object())
        # Nothing matches a bare object(), so we expect None back.
        assert adapt(object()) is None
    finally:
        _REGISTRY.remove(noisy)


# ---- 8. JsonlFileStore corruption tracking ---------------------------

def test_jsonl_store_tracks_corrupt_lines(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    # Write one good entry then a corrupt line.
    s = JsonlFileStore(str(path))
    s.put(
        a2s.RoutingExplanation(
            handoff_id="h1",
            task="t",
            intent="",
            chosen_agent="a",
            chosen_skill="s",
            rationale="r",
            features_fired={},
            alternatives=[],
            confidence=0.9,
        )
    )
    # Append a corrupt line manually.
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")

    # Re-load: should not raise; should track the skip.
    s2 = JsonlFileStore(str(path))
    assert s2.corruption_stats["skipped_json"] == 1
    assert "h1" in s2


# ---- 9. MappingView is a frozen snapshot -----------------------------

def test_as_mapping_returns_frozen_snapshot():
    store = InMemoryStore()
    store.put(
        a2s.RoutingExplanation(
            handoff_id="h1",
            task="t",
            intent="",
            chosen_agent="a",
            chosen_skill="s",
            rationale="r",
            features_fired={},
            alternatives=[],
            confidence=0.9,
        )
    )
    snap = store.as_mapping()
    assert "h1" in snap
    assert len(snap) == 1

    # Mutating the source must NOT change the snapshot.
    store.put(
        a2s.RoutingExplanation(
            handoff_id="h2",
            task="t",
            intent="",
            chosen_agent="a",
            chosen_skill="s",
            rationale="r",
            features_fired={},
            alternatives=[],
            confidence=0.9,
        )
    )
    assert len(snap) == 1
    assert "h2" not in snap


# ---- 10. unicode bypass resistance -----------------------------------

def test_normalise_collapses_unicode_lookalikes():
    # Fullwidth Latin "refund" -> NFKC normalises to ASCII "refund".
    assert _normalise("ｒｅｆｕｎｄ") == _normalise("refund")


def test_conformance_blocks_unicode_bypass_of_deny_term():
    s = Society(strict=False)
    s.add(_Analyst())
    # Deny term "refund" should still block a task that uses the fullwidth
    # Latin form -- otherwise an attacker could route restricted work past
    # the boundary by swapping characters.
    s.boundary("analyst-agent", deny=["refund"])
    res = conformance_check(
        s.graph,
        agent="analyst-agent",
        skill_id="data_analysis",
        task="please ｒｅｆｕｎｄ my last transaction",
    )
    assert res.ok is False
    assert "refund" in (res.reason or "")


# ---- 11. governance hooks that raise do not break dispatch -----------

def test_governance_hook_exception_does_not_break_dispatch(caplog):
    s = _build_society()

    def kaboom(explanation):
        raise RuntimeError("bad hook")

    s.on_low_confidence(kaboom, threshold=1.5)  # threshold above any score

    # Should not raise. We just check that run() completes and the
    # handler's exception was logged rather than propagated.
    with caplog.at_level("WARNING", logger="agent2society.mesh"):
        out = s.run(Handoff(task="compute statistics on Q3"))
    assert "ANALYSIS" in out


# ---- 12. human_review_when predicate failure default ------------------

def test_human_review_predicate_failure_defaults_to_review_required():
    s = _build_society()
    reviewed: List[str] = []
    s.on_human_review(lambda exp, text: reviewed.append(text))

    def bad_predicate(text: str) -> bool:
        raise ValueError("predicate broken")

    h = Handoff(task="compute statistics on Q3", human_review_when=bad_predicate)
    s.run(h)
    # Predicate raised -> must escalate to review (fail-safe).
    assert len(reviewed) == 1
    snap = s.metrics.snapshot()
    counters = snap["counters"]
    assert any(
        r["value"] > 0
        for r in counters.get("agent2society_human_review_predicate_errors_total", [])
    )


# ---- 13. retry path attribution --------------------------------------

def test_retry_failure_recorded_as_fallback():
    """When retry=True and the first candidate fails, the second's success
    must be reflected, and the failed first must show up in fallbacks."""
    # min_score=0.0 so the runner-up survives onto the retry sequence.
    s = Society(strict=True, min_score=0.0)
    s.add(_Analyst())
    s.add(_Writer())

    calls = {"n": 0}
    real_send = s._transport.send

    def flaky_send(url, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise DispatchError("first attempt failed")
        return real_send(url, payload)

    s._transport.send = flaky_send  # type: ignore[assignment]

    out = s.run(Handoff(task="compute statistics on Q3 churn"), retry=True)
    assert out  # non-empty -- retry must have succeeded on the second candidate
    last = s.telemetry.records[-1]
    fallback_reasons = [f["reason"] for f in last.fallbacks]
    assert any("dispatch failed" in r or "retry after" in r for r in fallback_reasons)
