"""Sentence-transformers embedding adapter — no external service needed.

Uses all-MiniLM-L6-v2 by default (22MB, 384-dim, ~14k tokens/sec on CPU).
Swap model_name for a larger model in production (e.g. all-mpnet-base-v2).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class SentenceTransformerEmbedder:
    """Implements ports.storage.Embedder using sentence-transformers (local, no API key)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required: pip install sentence-transformers"
            ) from exc
        self._model: Any = SentenceTransformer(model_name)
        self._model_name = model_name
        log.info("SentenceTransformerEmbedder loaded model=%r", model_name)

    @property
    def model_id(self) -> str:
        return self._model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return vecs.tolist()
