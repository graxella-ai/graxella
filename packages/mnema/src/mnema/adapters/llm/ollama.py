from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from mnema.config import settings
from mnema.ports.llm import ContradictionProposal, LLMCallError, RuleProposal, SkillProposal

try:
    from pydantic import ValidationError
except ImportError:  # pragma: no cover
    ValidationError = Exception  # type: ignore[misc,assignment]

log = logging.getLogger(__name__)


_MAX_STATEMENT_LEN = 500  # chars — prevent prompt bloat / injection padding


def _sanitize_assertions(assertions: list[dict]) -> list[dict]:
    """Strip control characters and truncate statement values before they reach the LLM prompt.

    Protects against prompt injection via crafted assertion statement text.
    """
    import re
    _ctrl = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
    sanitized = []
    for a in assertions:
        cleaned = dict(a)
        stmt = str(cleaned.get("statement", ""))
        stmt = _ctrl.sub("", stmt)[:_MAX_STATEMENT_LEN]
        cleaned["statement"] = stmt
        sanitized.append(cleaned)
    return sanitized


class OllamaLLM:
    """LLM adapter that calls Ollama local REST API. Uses stdlib urllib only."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._model = model or settings.ollama_model
        self._base_url = (base_url or settings.ollama_host).rstrip("/")
        self._timeout = timeout if timeout is not None else settings.ollama_timeout

    def _call(self, prompt: str) -> str:
        """POST to Ollama /api/generate and return the response text."""
        body = json.dumps({"model": self._model, "prompt": prompt, "stream": False}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise LLMCallError(f"Ollama network error: {exc}") from exc
        try:
            return json.loads(raw)["response"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise LLMCallError(
                f"Ollama returned unexpected payload: {exc}\nraw={raw[:200]}"
            ) from exc

    def extract_rules(
        self,
        assertions: list[dict],
        *,
        max_rules: int = 10,
    ) -> list[RuleProposal]:
        """Call Ollama to extract imperative rules from assertion observations."""
        assertions = _sanitize_assertions(assertions)
        prompt = (
            f"You are a knowledge consolidator. Given these agent observations, "
            f"extract up to {max_rules} imperative rules.\n"
            "Each rule must be a single sentence stating what the agent should do or avoid.\n\n"
            f"Observations (JSON):\n{json.dumps(assertions, indent=2)}\n\n"
            "Respond with a JSON array of objects:\n"
            '[{"text": "...", "scope": "...", "confidence": 0.0-1.0, '
            '"derived_from": ["assertion_id", ...]}]\n'
            "Respond ONLY with the JSON array. No explanation."
        )
        raw = self._call(prompt)
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMCallError(
                f"OllamaLLM.extract_rules: LLM returned non-JSON: {exc}\nraw={raw[:300]}"
            ) from exc
        if not isinstance(items, list):
            raise LLMCallError(
                f"OllamaLLM.extract_rules: expected JSON array, got {type(items).__name__}"
            )

        results: list[RuleProposal] = []
        for item in items:
            try:
                results.append(RuleProposal(**item))
            except (ValidationError, TypeError) as exc:
                log.warning("OllamaLLM.extract_rules: skipping invalid item %r: %s", item, exc)
            if len(results) >= max_rules:
                break
        return results

    def extract_skills(
        self,
        assertions: list[dict],
        *,
        max_skills: int = 5,
    ) -> list[SkillProposal]:
        """Call Ollama to extract reusable Python skill functions from observations."""
        assertions = _sanitize_assertions(assertions)
        prompt = (
            f"You are a skill crystalliser. Extract up to {max_skills} reusable Python functions "
            "from these observations.\n\n"
            f"Observations (JSON):\n{json.dumps(assertions, indent=2)}\n\n"
            "Respond with a JSON array:\n"
            '[{"name": "snake_case_name", "description": "...", '
            '"code": "def name(...):\\n    ...", '
            '"signature": "def name(...) -> ...", "derived_from": ["id"]}]\n'
            "Respond ONLY with the JSON array."
        )
        raw = self._call(prompt)
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMCallError(
                f"OllamaLLM.extract_skills: LLM returned non-JSON: {exc}\nraw={raw[:300]}"
            ) from exc
        if not isinstance(items, list):
            raise LLMCallError(
                f"OllamaLLM.extract_skills: expected JSON array, got {type(items).__name__}"
            )

        results: list[SkillProposal] = []
        for item in items:
            try:
                results.append(SkillProposal(**item))
            except (ValidationError, TypeError) as exc:
                log.warning("OllamaLLM.extract_skills: skipping invalid item %r: %s", item, exc)
            if len(results) >= max_skills:
                break
        return results

    def detect_contradictions(
        self,
        assertions: list[dict],
    ) -> list[ContradictionProposal]:
        """Call Ollama to find pairs of assertions that logically contradict each other."""
        assertions = _sanitize_assertions(assertions)
        prompt = (
            "You are a knowledge auditor. Find pairs of assertions that directly contradict "
            "each other — different factual claims about the same subject.\n\n"
            f"Assertions (JSON):\n{json.dumps(assertions, indent=2)}\n\n"
            "Respond with a JSON array of contradiction pairs. "
            "Only include clear factual conflicts, not differences of scope or emphasis.\n"
            '[{"from_id": "...", "to_id": "...", "reason": "...", "confidence": 0.0-1.0}]\n'
            "Respond ONLY with the JSON array. If no contradictions exist, respond with []."
        )
        raw = self._call(prompt)
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMCallError(
                f"OllamaLLM.detect_contradictions: LLM returned non-JSON: {exc}\nraw={raw[:300]}"
            ) from exc
        if not isinstance(items, list):
            raise LLMCallError(
                f"OllamaLLM.detect_contradictions: expected JSON array, got {type(items).__name__}"
            )

        results: list[ContradictionProposal] = []
        for item in items:
            try:
                results.append(ContradictionProposal(**item))
            except (ValidationError, TypeError) as exc:
                log.warning(
                    "OllamaLLM.detect_contradictions: skipping invalid item %r: %s", item, exc
                )
        return results

    def answer_question(self, context: str, question: str) -> str:
        """Ask the LLM to answer a question given a context string.

        Used by benchmarks (LongMemEval) where the injected Digest is the context.
        Returns the raw answer text, stripped of whitespace.
        """
        prompt = (
            "You are a precise question-answering assistant.\n"
            "Answer the question using ONLY the information in the context below.\n"
            "Give a short, direct answer — one sentence or a few words.\n"
            "If the context does not contain the answer, respond with exactly: unknown\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {question}\n"
            "Answer:"
        )
        return self._call(prompt).strip()

    def health_check(self) -> bool:
        """GET /api/tags — return True if Ollama is reachable."""
        req = urllib.request.Request(
            f"{self._base_url}/api/tags",
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status == 200
        except urllib.error.URLError:
            return False
