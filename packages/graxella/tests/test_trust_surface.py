"""Workstream 0C — trust surface: persistent-by-default stores and loud
failures. A reliability product may not default to amnesia, and may not
swallow its own degradation."""
from __future__ import annotations

import logging

import pytest

import graxella
from graxella.beliefs import Memory


def billing_agent(payload):
    """decide refund eligibility for billing complaints and orders"""
    return {"result": f"handled {payload}"}


def email_agent(payload):
    """write a friendly response email to the customer"""
    return {"result": f"drafted {payload}"}


# -- 0C-4: persistent by default --------------------------------------------

def test_default_memory_is_persistent_in_workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = graxella.mesh([billing_agent, email_agent])
    app.route("billing refund order 1")
    assert (tmp_path / ".graxella" / "mnema.db").exists()
    assert (tmp_path / ".graxella" / "mesh-routes.jsonl").exists()
    # No CWD pollution outside the workdir.
    stray = [p.name for p in tmp_path.iterdir() if p.name != ".graxella"]
    assert stray == []

    # A SECOND mesh in the same project sees the first run's evidence —
    # persistence across constructions is the default, not an option.
    app2 = graxella.mesh([billing_agent, email_agent])
    stats = app2.memory.outcome_stats()
    assert stats["total"]["count"] == 1


def test_ephemeral_is_explicit_and_loud(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.WARNING, logger="graxella"):
        app = graxella.mesh([billing_agent, email_agent], memory="ephemeral",
                            store_path=str(tmp_path / "r.jsonl"))
    assert any("EPHEMERAL" in r.message for r in caplog.records)
    app.route("billing refund order 1")
    assert not (tmp_path / ".graxella" / "mnema.db").exists()


# -- 0C-3: loud failures -----------------------------------------------------

class _BrokenSupervisorLLM:
    def invoke(self, messages):
        raise ConnectionError("ollama not reachable")


def test_supervisor_llm_failure_is_flagged_not_silent(tmp_path, caplog):
    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="t")
    app = graxella.supervisor([billing_agent, email_agent],
                              _BrokenSupervisorLLM(),
                              memory=memory,
                              store_path=str(tmp_path / "r.jsonl"))
    with caplog.at_level(logging.WARNING, logger="graxella"):
        out = app.invoke("billing refund order 5")
    # The caller paid for LLM routing and didn't get it — three signals:
    assert out["route"]["supervisor_fallback"] is True            # in the result
    assert any("falling back" in r.message for r in caplog.records)  # in the log
    events = app.tracer.events(source="orchestrator",
                               event_type="degradation.supervisor_fallback")
    assert len(events) == 1                                        # in the tracer
    assert "ConnectionError" in events[0].payload["err"]
    # And the dispatch itself still succeeded deterministically.
    assert out["route"]["agent"] == "billing_agent"


def test_supervisor_miss_is_not_an_error(tmp_path):
    class VagueLLM:
        def invoke(self, messages):
            class R:  # answered, but named nobody we know
                content = "hmm, maybe the accounting department?"
            return R()

    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="t")
    app = graxella.supervisor([billing_agent, email_agent], VagueLLM(),
                              memory=memory,
                              store_path=str(tmp_path / "r.jsonl"))
    out = app.invoke("billing refund order 6")
    assert out["route"]["supervisor_fallback"] is False
    assert app.tracer.events(event_type="degradation.supervisor_fallback") == []


def test_broken_tracer_hook_logs_but_never_breaks_writes(tmp_path, caplog):
    memory = Memory.sqlite(str(tmp_path / "m.db"), agent_id="t")
    memory.attach_tracer(lambda et, payload: 1 / 0)
    with caplog.at_level(logging.WARNING, logger="graxella"):
        aid = memory.record_decision(decision_type="delegate", task="t",
                                     chosen="a::s")
    assert aid.startswith("asr_")                       # the write survived
    assert any("tracer hook failed" in r.message for r in caplog.records)
