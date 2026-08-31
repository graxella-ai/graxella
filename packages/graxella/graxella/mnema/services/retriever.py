from __future__ import annotations

from datetime import datetime
from typing import Any

from graxella.mnema.config import settings
from graxella.mnema.core.models import Assertion


class MemoryRetriever:
    """Read-side facade over the memory store.

    Pass an Embedder via `embedder=` to enable semantic_search().
    Omit it (or pass None) to use subject-filter search only.
    """

    def __init__(self, store: Any, *, embedder: Any | None = None) -> None:
        self._store = store
        self._embedder = embedder

    def render(
        self,
        agent_id: str,
        *,
        namespace: str = settings.default_namespace,
        max_chars: int = settings.digest_max_chars,
    ) -> str:
        """Render the latest digest as a markdown string for prompt injection."""
        digest = self._store.latest_digest(namespace=namespace, agent_id=agent_id)
        if digest is None:
            return ""
        return digest.render(max_chars=max_chars)

    def search(
        self,
        agent_id: str,
        query_subject: str,
        *,
        namespace: str = settings.default_namespace,
        as_of: datetime | None = None,
    ) -> list[Assertion]:
        """Return current beliefs filtered by exact subject tag."""
        return self._store.current_beliefs(
            namespace=namespace,
            agent_id=agent_id,
            subject=query_subject,
            as_of=as_of,
        )

    def semantic_search(
        self,
        agent_id: str,
        query_text: str,
        *,
        namespace: str = settings.default_namespace,
        top_k: int = 5,
        as_of: datetime | None = None,
    ) -> list[tuple[float, Assertion]]:
        """Return top-k assertions ranked by similarity to query_text.

        Uses the injected Embedder if available, otherwise falls back to
        TfidfEmbedder (always installed — pure Python + stdlib math).
        Returns list of (score, assertion) sorted descending by score.
        """
        beliefs = self._store.current_beliefs(
            namespace=namespace,
            agent_id=agent_id,
            as_of=as_of,
        )
        if not beliefs:
            return []

        embedder = self._embedder
        if embedder is None:
            from graxella.mnema.adapters.embedder.tfidf import TfidfEmbedder
            embedder = TfidfEmbedder()

        statements = [b.statement for b in beliefs]
        # Embed query + corpus together so TF-IDF IDF is computed over the full set
        all_texts = [query_text] + statements
        all_vecs = embedder.embed(all_texts)
        q_vec = all_vecs[0]
        doc_vecs = all_vecs[1:]

        scores = [_cosine(q_vec, d) for d in doc_vecs]
        ranked = sorted(zip(scores, beliefs, strict=True), key=lambda x: x[0], reverse=True)
        return [(score, belief) for score, belief in ranked[:top_k]]

    def belief_count(self, agent_id: str, *, namespace: str = settings.default_namespace) -> int:
        """Count of non-retracted, non-superseded beliefs for the agent."""
        return len(self._store.current_beliefs(namespace=namespace, agent_id=agent_id))

    def latest_digest_version(
        self, agent_id: str, *, namespace: str = settings.default_namespace
    ) -> int | None:
        """Version number of the latest digest, or None if no digest exists."""
        digest = self._store.latest_digest(namespace=namespace, agent_id=agent_id)
        return digest.version if digest is not None else None


def _cosine(a: list[float], b: list[float]) -> float:
    """Dot product of two already-normalized vectors (cosine similarity)."""
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))
