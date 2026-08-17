"""Default similarity scoring for agent2society.

The product claim is "one cheap embedding lookup instead of a supervisor LLM."
The default implementation here is a deterministic, dependency-free
TF-IDF + cosine similarity over skill text. It's good enough to demonstrate
the wedge on the first run, and trivially swappable for a real embedding
model via `Mesh(embed_fn=...)`.

`embed_fn` contract:
    embed_fn(texts: list[str]) -> list[list[float]]
"""
from __future__ import annotations

import math
import re
from typing import Callable, List, Sequence

# An embedding function returns one vector per input string.
EmbedFn = Callable[[Sequence[str]], List[List[float]]]


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


class TfidfEmbedder:
    """Deterministic TF-IDF embedder.

    Fitted once over the corpus of skill texts. `embed(query)` produces a
    vector in the same vocabulary space, so cosine similarity is meaningful.
    """

    def __init__(self) -> None:
        self.vocab: dict[str, int] = {}
        self.idf: List[float] = []
        self._fitted = False

    def fit(self, corpus: Sequence[str]) -> "TfidfEmbedder":
        df: dict[str, int] = {}
        docs = [tokenize(c) for c in corpus]
        n = max(len(docs), 1)
        for doc in docs:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1
        # Stable vocab order: by frequency desc, then alphabetic.
        terms = sorted(df.items(), key=lambda kv: (-kv[1], kv[0]))
        self.vocab = {t: i for i, (t, _) in enumerate(terms)}
        self.idf = [
            math.log((1 + n) / (1 + df[t])) + 1.0
            for t, _ in terms
        ]
        self._fitted = True
        return self

    def embed_one(self, text: str) -> List[float]:
        vec = [0.0] * len(self.vocab)
        if not self._fitted or not self.vocab:
            return vec
        tokens = tokenize(text)
        if not tokens:
            return vec
        tf: dict[int, float] = {}
        for t in tokens:
            i = self.vocab.get(t)
            if i is None:
                continue
            tf[i] = tf.get(i, 0.0) + 1.0
        length = float(len(tokens))
        for i, c in tf.items():
            vec[i] = (c / length) * self.idf[i]
        # L2 normalise so dot product == cosine.
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.embed_one(t) for t in texts]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    # Both vectors are already L2-normalised in TfidfEmbedder, but be safe
    # for externally supplied embed_fn results.
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
