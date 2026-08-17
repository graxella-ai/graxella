"""Shared fixtures."""
from __future__ import annotations

import pytest

import agent2society as r2r


def _card(name: str, skills):
    return {
        "name": name,
        "description": f"{name} agent",
        "url": f"local://{name}",
        "version": "0.0.1",
        "skills": skills,
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
    }


@pytest.fixture
def research_card():
    return _card(
        "research-agent",
        [
            {
                "id": "web_research",
                "name": "Web Research",
                "description": (
                    "Search the web, gather sources, summarise findings on a topic."
                ),
                "tags": ["research", "search", "web", "sources"],
                "examples": ["research the Q3 churn drivers"],
            }
        ],
    )


@pytest.fixture
def writer_card():
    return _card(
        "writer-agent",
        [
            {
                "id": "exec_memo",
                "name": "Executive Memo",
                "description": (
                    "Draft executive memos and short business writing from notes."
                ),
                "tags": ["writing", "memo", "exec", "draft"],
                "examples": ["draft an exec memo about churn"],
            }
        ],
    )


@pytest.fixture
def coder_card():
    return _card(
        "coder-agent",
        [
            {
                "id": "python_code",
                "name": "Write Python",
                "description": "Write Python code, refactor, debug.",
                "tags": ["code", "python", "programming"],
            }
        ],
    )
