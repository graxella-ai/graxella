"""TaskAgent: rule-based agent that uses memory to answer weatherlib API tasks.

Three arms:
  A0 - Baseline: no memory, always uses v1 API (hardcoded initial knowledge)
  A1 - Simple retrieval: stores observations, retrieves by subject keyword match
  A2 - Mnema: full sleep-phase consolidation into Rules, injects rules at query time
"""

from __future__ import annotations

import re
from typing import Any

from experiments.codedrift import Task


# ── A0: Baseline (no memory, static knowledge) ────────────────────────────────

class A0BaselineAgent:
    """Knows only v1 API. Never updates. Represents a frozen LLM without memory."""

    STATIC_KNOWLEDGE = "weatherlib.get_weather(city)"

    def answer(self, task: Task) -> str:
        return self.STATIC_KNOWLEDGE

    def observe(self, observation: str, *, subject: str = "weatherlib") -> None:
        pass  # ignores all observations

    def sleep_consolidate(self) -> None:
        pass


# ── A1: Simple retrieval (observations stored, keyword retrieval) ──────────────

class A1SimpleRetrievalAgent:
    """Stores raw observations, retrieves by keyword match. No consolidation."""

    def __init__(self) -> None:
        self._observations: list[str] = []
        self._default = "weatherlib.get_weather(city)"

    def observe(self, observation: str, *, subject: str = "weatherlib") -> None:
        self._observations.append(observation)

    def sleep_consolidate(self) -> None:
        pass  # A1 does not consolidate

    def answer(self, task: Task) -> str:
        # Naive retrieval: scan all observations, return last API pattern found.
        # Does NOT understand context -- error messages and migration notes confuse it.
        api_patterns = [
            r"weatherlib\.WeatherClient\(\)\.fetch\(city\)",
            r"weatherlib\.fetch_forecast\(city\)",
            r"weatherlib\.get_weather\(city\)",
        ]
        last_match: str | None = None
        for obs in self._observations:
            for pat in api_patterns:
                m = re.search(pat, obs)
                if m:
                    last_match = m.group(0)  # overwrite with every match, including errors
        return last_match if last_match is not None else self._default


# ── A2: Mnema agent (sleep-phase consolidation + rule injection) ───────────────

class A2MnemaAgent:
    """Full Mnema memory: observe -> consolidate -> inject rules -> answer."""

    def __init__(self, store: Any, llm: Any) -> None:
        from mnema.services.recorder import MemoryRecorder
        from mnema.services.consolidator import SleepConsolidator
        from mnema.services.retriever import MemoryRetriever

        self._store = store
        self._llm = llm
        self._agent_id = "e1-agent"
        self._recorder = MemoryRecorder(store)
        self._consolidator = SleepConsolidator(store, llm)
        self._retriever = MemoryRetriever(store)
        self._consolidated_rules: list[str] = []
        self._default = "weatherlib.get_weather(city)"

    def observe(self, observation: str, *, subject: str = "weatherlib") -> None:
        self._recorder.observe(self._agent_id, observation, subject=subject)

    def sleep_consolidate(self) -> None:
        digest = self._consolidator.consolidate(self._agent_id)
        if digest is not None:
            self._consolidated_rules = [r.text for r in digest.rules]

    def answer(self, task: Task) -> str:
        # Use injected rules to pick the best API call
        api_patterns = [
            (r"WeatherClient\(\)\.fetch", "weatherlib.WeatherClient().fetch(city)"),
            (r"fetch_forecast", "weatherlib.fetch_forecast(city)"),
            (r"get_weather", "weatherlib.get_weather(city)"),
        ]
        for rule_text in self._consolidated_rules:
            for pat, answer in api_patterns:
                if re.search(pat, rule_text):
                    return answer
        return self._default
