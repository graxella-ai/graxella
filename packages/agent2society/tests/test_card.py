from __future__ import annotations

import json
import os
import tempfile

import pytest

import agent2society as r2r
from agent2society.exceptions import AgentCardError


def test_parse_minimal():
    card = r2r.parse_card(
        {
            "name": "x",
            "url": "https://x.example/a2a",
            "skills": [{"id": "s1", "name": "skill one"}],
        }
    )
    assert card.name == "x"
    assert card.url == "https://x.example/a2a"
    assert card.declares("s1")
    assert not card.declares("nope")


def test_parse_rejects_missing_required():
    with pytest.raises(AgentCardError):
        r2r.parse_card({"name": "x"})
    with pytest.raises(AgentCardError):
        r2r.parse_card({"url": "https://x"})


def test_parse_snake_case_compat():
    card = r2r.parse_card(
        {
            "name": "x",
            "url": "u",
            "skills": [
                {
                    "id": "s",
                    "name": "s",
                    "input_modes": ["text"],
                    "output_modes": ["text"],
                }
            ],
            "default_input_modes": ["text"],
            "default_output_modes": ["text"],
        }
    )
    assert card.default_input_modes == ["text"]
    assert card.skills[0].input_modes == ["text"]


def test_load_card_from_path(tmp_path):
    p = tmp_path / "card.json"
    p.write_text(
        json.dumps(
            {
                "name": "x",
                "url": "u",
                "skills": [{"id": "s", "name": "s"}],
            }
        )
    )
    card = r2r.load_card(str(p))
    assert card.name == "x"


def test_load_card_passthrough_agentcard(research_card):
    card1 = r2r.load_card(research_card)
    card2 = r2r.load_card(card1)
    assert card1 is card2
