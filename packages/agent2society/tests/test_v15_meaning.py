"""v1.5 meaning surfaces: SelfAssessment, Handoff, RoutingExplanation.

These tests validate the typed envelope and the human-readable
explanation that turn agent2society from a router into a transparent
coordination layer.
"""
from __future__ import annotations

import pytest

import agent2society
from agent2society import (
    DecisionRecord,
    Handoff,
    Mesh,
    RoutingExplanation,
    SelfAssessment,
)


# --- card parsing ----------------------------------------------------------


def test_self_assessment_parses_from_card_json():
    card = agent2society.parse_card(
        {
            "name": "writer-agent",
            "url": "local://writer",
            "skills": [{"id": "exec_memo", "name": "Exec Memo"}],
            "selfAssessment": {
                "confidenceModel": "tfidf_score",
                "knownLimitations": ["cannot translate"],
                "outOfScope": ["legal advice"],
                "escalateWhen": ["unverifiable sources cited"],
            },
        }
    )
    assert card.self_assessment is not None
    sa = card.self_assessment
    assert sa.confidence_model == "tfidf_score"
    assert "cannot translate" in sa.known_limitations
    assert "legal advice" in sa.out_of_scope
    assert "unverifiable sources cited" in sa.escalate_when


def test_card_without_self_assessment_still_parses():
    card = agent2society.parse_card(
        {
            "name": "x",
            "url": "u",
            "skills": [{"id": "s", "name": "s"}],
        }
    )
    assert card.self_assessment is None


def test_self_assessment_snake_case_compat():
    card = agent2society.parse_card(
        {
            "name": "x",
            "url": "u",
            "skills": [{"id": "s", "name": "s"}],
            "self_assessment": {
                "confidence_model": "self_report",
                "known_limitations": ["lim"],
            },
        }
    )
    assert card.self_assessment is not None
    assert card.self_assessment.confidence_model == "self_report"
    assert card.self_assessment.known_limitations == ["lim"]


def test_self_assessment_is_empty_helper():
    assert SelfAssessment().is_empty()
    assert not SelfAssessment(known_limitations=["x"]).is_empty()


# --- Handoff envelope ------------------------------------------------------


def test_handoff_from_string_round_trip():
    h = Handoff.from_string("draft a memo")
    assert h.task == "draft a memo"
    assert h.intent == ""
    assert h.prior == []
    assert h.id


def test_handoff_extend_carries_prior_decisions():
    h0 = Handoff(task="research churn", intent="Q3 board prep")
    h1 = h0.extend(
        agent="research-agent",
        skill="web_research",
        summary="found 3 churn drivers",
        confidence=0.82,
        next_task="draft a memo on those drivers",
    )
    assert h1.task == "draft a memo on those drivers"
    assert h1.intent == "Q3 board prep"   # carried forward
    assert len(h1.prior) == 1
    step = h1.prior[0]
    assert isinstance(step, DecisionRecord)
    assert step.agent == "research-agent"
    assert step.confidence == 0.82
    assert h1.id != h0.id                  # new id per step


def test_handoff_to_dict_is_serialisable():
    h = Handoff(
        task="x",
        intent="y",
        assumptions=["a"],
        prior=[DecisionRecord(agent="r", skill="s", summary="ok")],
    )
    d = h.to_dict()
    assert d["task"] == "x"
    assert d["intent"] == "y"
    assert d["prior"][0]["agent"] == "r"
    assert "id" in d


# --- mesh.run accepts Handoff and string both -----------------------------


class FakeWriter:
    name = "writer-agent"
    description = "drafts executive memos"
    skills = [
        {
            "id": "exec_memo",
            "name": "Executive Memo",
            "description": "Drafts executive memos from notes.",
            "tags": ["writing", "memo", "exec"],
        }
    ]

    def run(self, task):
        return f"MEMO: {task}"


def test_mesh_run_accepts_string_still_works():
    m = Mesh()
    m.add(FakeWriter())
    out = m.run("Draft an executive memo on churn")
    assert "MEMO" in out


def test_mesh_run_accepts_handoff_and_stores_explanation():
    m = Mesh()
    m.add(FakeWriter())
    h = Handoff(
        task="Draft an executive memo on Q3 churn",
        intent="prep the board pack",
        assumptions=["churn data is final"],
    )
    out = m.run(h)
    assert "MEMO" in out
    exp = m.explain(h.id)
    assert exp is not None
    assert exp.chosen_agent == "writer-agent"
    assert exp.chosen_skill == "exec_memo"
    assert exp.intent == "prep the board pack"
    assert "churn data is final" in exp.assumptions


# --- RoutingExplanation surface -------------------------------------------


def test_explanation_includes_features_alternatives_and_rationale():
    m = Mesh()
    m.add(FakeWriter())
    h = Handoff(task="Draft an exec memo")
    m.run(h)
    exp = m.explain(h.id)
    assert isinstance(exp, RoutingExplanation)
    # Rationale is a non-empty natural-language sentence.
    assert exp.rationale and len(exp.rationale) > 20
    # Features fired are present.
    assert "score" in exp.features_fired
    assert "semantic" in exp.features_fired
    assert "tag_overlap" in exp.features_fired
    # Alternatives carry score AND audit fields.
    assert exp.alternatives
    top = exp.alternatives[0]
    assert top.agent == "writer-agent"
    assert isinstance(top.matched_tokens, list)
    assert isinstance(top.matched_tags, list)


def test_explanation_surfaces_self_assessment_caveats():
    card = agent2society.parse_card(
        {
            "name": "writer-agent",
            "url": "local://writer",
            "description": "drafts memos",
            "skills": [
                {
                    "id": "exec_memo",
                    "name": "Exec Memo",
                    "tags": ["writing", "memo"],
                }
            ],
            "selfAssessment": {
                "knownLimitations": ["only English"],
                "escalateWhen": ["any financial figure cited"],
            },
        }
    )

    def writer_handler(_url, payload):
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id", "0"),
            "result": {"parts": [{"kind": "text", "text": "ok"}]},
        }

    m = Mesh()
    m.add(card)
    m._local.register(card.url, writer_handler)
    h = Handoff(task="Draft an executive memo on churn")
    m.run(h)
    exp = m.explain(h.id)
    assert exp is not None
    caveats = exp.agent_self_caveats
    assert any("only English" in c for c in caveats)
    assert any("any financial figure cited" in c for c in caveats)


def test_explanation_renders_safely_on_windows_console():
    m = Mesh()
    m.add(FakeWriter())
    h = Handoff(task="Draft a memo", intent="board prep")
    m.run(h)
    exp = m.explain(h.id)
    rendered = exp.render()
    # Must be ASCII-safe -- cp1252-compatible -- so it prints on a
    # default Windows shell without UnicodeEncodeError.
    rendered.encode("cp1252")
    assert "writer-agent" in rendered
    assert "chose" in rendered
    assert "why" in rendered


def test_explanation_records_blocked_reason_when_conformance_fails():
    m = Mesh(strict=False)
    m.add(FakeWriter())
    m.boundary("writer-agent", deny=["financial"])
    h = Handoff(task="Draft a financial memo")
    out = m.run(h)
    assert out == ""
    exp = m.explain(h.id)
    assert exp is not None
    assert exp.chosen_agent is None
    assert exp.blocked_reason is not None
    assert "conformance" in exp.blocked_reason


# --- backward-compat: bare-string runs still get an explanation -----------


def test_string_task_still_produces_an_explanation():
    m = Mesh()
    m.add(FakeWriter())
    m.run("Draft an exec memo")
    last = m.last_explanation()
    assert last is not None
    assert last.chosen_agent == "writer-agent"
