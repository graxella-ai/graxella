"""TF-IDF bag-of-words embedder — zero external dependencies beyond numpy.

This is the built-in fallback used when sentence-transformers is unavailable
(e.g. Windows Long Path issue). It produces cosine-comparable vectors over a
vocabulary built from the corpus at embed() time — good enough to rank
assertions by keyword overlap with a structured query.

In production, replace with SentenceTransformerEmbedder for true semantic
similarity across paraphrase and domain-shift queries.
"""

from __future__ import annotations

import math
import re

_STOPWORDS = frozenset([
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "for", "of", "in", "on",
    "at", "to", "by", "with", "from", "and", "or", "not", "but", "if",
    "this", "that", "it", "its",
])


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


class TfidfEmbedder:
    """Corpus-level TF-IDF with cosine normalization.

    Call embed() with a batch of texts — the vocabulary is built from that batch.
    For semantic_search, the query is appended to the corpus so it shares the IDF.
    """

    model_id = "tfidf-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        tokenized = [_tokenize(t) for t in texts]
        vocab: dict[str, int] = {}
        for tokens in tokenized:
            for tok in tokens:
                if tok not in vocab:
                    vocab[tok] = len(vocab)

        n_docs = len(tokenized)
        n_terms = len(vocab)

        # document frequency
        df = [0] * n_terms
        for tokens in tokenized:
            for tok in set(tokens):
                df[vocab[tok]] += 1

        idf = [
            math.log((n_docs + 1) / (df[i] + 1)) + 1.0
            for i in range(n_terms)
        ]

        vecs: list[list[float]] = []
        for tokens in tokenized:
            tf: dict[int, float] = {}
            for tok in tokens:
                idx = vocab[tok]
                tf[idx] = tf.get(idx, 0.0) + 1.0
            raw = [tf.get(i, 0.0) * idf[i] for i in range(n_terms)]
            norm = math.sqrt(sum(x * x for x in raw)) or 1.0
            vecs.append([x / norm for x in raw])

        return vecs
