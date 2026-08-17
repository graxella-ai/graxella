from __future__ import annotations

import pytest

import agent2society as r2r


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

    def __init__(self):
        self.calls = []

    def run(self, task):
        self.calls.append(task)
        return f"MEMO: {task}"


class FakeResearcher:
    name = "research-agent"
    description = "searches and summarises"
    skills = [
        {
            "id": "web_research",
            "name": "Web Research",
            "description": "Web research and summary.",
            "tags": ["research", "web", "search"],
        }
    ]

    def __init__(self):
        self.calls = []

    def __call__(self, task):
        self.calls.append(task)
        return f"FINDINGS: {task}"


def test_end_to_end_via_callable_adapter():
    writer = FakeWriter()
    researcher = FakeResearcher()
    m = r2r.Mesh()
    m.add(researcher)
    m.add(writer)

    result = m.run("Draft an executive memo on Q3 churn")
    assert "MEMO" in result
    assert writer.calls and not researcher.calls

    result2 = m.run("Research Q3 churn drivers from the web")
    assert "FINDINGS" in result2
    assert researcher.calls


def test_report_records_dispatch_and_tokens():
    writer = FakeWriter()
    m = r2r.Mesh()
    m.add(writer)
    m.run("draft an exec memo")
    rendered = m.report(render=False)
    assert "dispatches=1" in rendered
    assert "writer-agent" in rendered
    s = m.telemetry.summary()
    assert s["dispatches"] == 1
    assert s["violations"] == 0
    assert s["total_tokens"] > 0


def test_violation_recorded_when_strict_false():
    writer = FakeWriter()
    m = r2r.Mesh(strict=False)
    m.add(writer)
    m.boundary("writer-agent", deny=["financial"])
    out = m.run("Draft a financial memo")
    assert out == ""
    assert len(m.telemetry.violations()) == 1


def test_no_route_when_mesh_is_empty():
    m = r2r.Mesh()
    with pytest.raises(r2r.NoRouteError):
        m.run("anything")


def test_describe_surface():
    writer = FakeWriter()
    m = r2r.Mesh()
    m.add(writer)
    m.boundary("writer-agent", deny=["pii"])
    desc = m.describe()
    assert desc[0]["name"] == "writer-agent"
    assert desc[0]["boundary"]["deny"] == ["pii"]
    assert any(s["id"] == "exec_memo" for s in desc[0]["skills"])
