"""Five test tasks with the ground-truth skill set each one should invoke.

Deliberately varied so different subsets of the 10-skill registry apply:
some tasks need one skill, some need two. That range is what exercises
the router — a single-skill task shouldn't drag in six irrelevant tool
schemas, and a two-skill task should get both right.

`intent` is a coarse label used for rulebook caching (same intent + same
query => cache hit; different intent => fresh routing).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Task:
    query: str
    intent: str
    expected_skills: frozenset[str]


TASKS: list[Task] = [
    Task(
        query="What's the weather in Tokyo tomorrow?",
        intent="assist",
        expected_skills=frozenset({"get_weather"}),
    ),
    Task(
        query="I need to fly from London to Paris on 2026-04-20 and find a hotel for three nights.",
        intent="assist",
        expected_skills=frozenset({"book_flight", "find_hotel"}),
    ),
    Task(
        query="What's 500 euros in Japanese yen and what's the current time in Tokyo?",
        intent="assist",
        expected_skills=frozenset({"convert_currency", "get_local_time"}),
    ),
    Task(
        query="Log a 45 dollar dinner expense for the trip.",
        intent="assist",
        expected_skills=frozenset({"calculate_expense"}),
    ),
    Task(
        query="How do I say 'thank you' in Japanese and do I need a visa from US to Japan?",
        intent="assist",
        expected_skills=frozenset({"translate_phrase", "check_visa"}),
    ),
]
