"""Ollama embedding adapter — dense semantic vectors via local Ollama REST API.

Uses stdlib urllib only (no new dependencies beyond OllamaLLM).
Recommended model: nomic-embed-text (768-dim, strong semantic understanding)
  ollama pull nomic-embed-text

Supports Ollama ≥0.1.26 batch endpoint (/api/embed) with automatic
fallback to legacy single-text endpoint (/api/embeddings) for older installs.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from mnema.config import settings

log = logging.getLogger(__name__)


class OllamaEmbedder:
    """Dense embedding adapter backed by Ollama's local REST API.

    Implements the ports.storage.Embedder protocol.
    Raises LLMCallError (from ports.llm) on network or parsing failures so
    the caller can fall back gracefully — never silently returns zero vectors.
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._model = model or settings.embed_model
        self._base_url = (base_url or settings.ollama_host).rstrip("/")
        self._timeout = timeout if timeout is not None else settings.ollama_timeout
        self._use_batch_api: bool | None = None  # discovered on first call

    @property
    def model_id(self) -> str:
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one normalized embedding vector per text.

        Tries Ollama ≥0.1.26 batch /api/embed first; falls back to
        legacy /api/embeddings (one call per text) for older installs.
        """
        if not texts:
            return []

        if self._use_batch_api is not False:
            try:
                vecs = self._embed_batch(texts)
                self._use_batch_api = True
                return vecs
            except _OllamaLegacyEndpoint:
                log.info(
                    "OllamaEmbedder: /api/embed not available, switching to legacy /api/embeddings"
                )
                self._use_batch_api = False

        return [self._embed_single(t) for t in texts]

    # ── private helpers ────────────────────────────────────────────────────────

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """POST /api/embed — Ollama ≥0.1.26 batch endpoint."""
        from mnema.ports.llm import LLMCallError

        body = json.dumps({"model": self._model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise _OllamaLegacyEndpoint from exc
            raise LLMCallError(f"OllamaEmbedder /api/embed HTTP {exc.code}: {exc}") from exc
        except urllib.error.URLError as exc:
            raise LLMCallError(f"OllamaEmbedder network error: {exc}") from exc

        try:
            data = json.loads(raw)
            embeddings = data["embeddings"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise LLMCallError(
                f"OllamaEmbedder /api/embed unexpected payload: {exc}\nraw={raw[:200]}"
            ) from exc

        if len(embeddings) != len(texts):
            raise LLMCallError(
                f"OllamaEmbedder: expected {len(texts)} embeddings, got {len(embeddings)}"
            )
        return [_normalize(v) for v in embeddings]

    def _embed_single(self, text: str) -> list[float]:
        """POST /api/embeddings — legacy single-text endpoint."""
        from mnema.ports.llm import LLMCallError

        body = json.dumps({"model": self._model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise LLMCallError(f"OllamaEmbedder network error: {exc}") from exc

        try:
            vec = json.loads(raw)["embedding"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise LLMCallError(
                f"OllamaEmbedder /api/embeddings unexpected payload: {exc}\nraw={raw[:200]}"
            ) from exc

        return _normalize(vec)

    def health_check(self) -> bool:
        """Return True if Ollama is reachable."""
        req = urllib.request.Request(f"{self._base_url}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status == 200
        except urllib.error.URLError:
            return False


class _OllamaLegacyEndpoint(Exception):
    """Internal signal: /api/embed returned 404, use legacy /api/embeddings instead."""


def _normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector in pure Python — no numpy needed."""
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]
