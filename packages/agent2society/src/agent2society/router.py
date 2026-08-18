"""Graph-routed dispatch.

A task arrives as free text (plus optional tags). The router scores it
against every (agent, skill) pair in the graph using a single embedding
lookup -- *not* a supervisor LLM that haggles in natural language.

The score has two parts:
  * semantic similarity (cosine over skill text vs task text)
  * a deterministic tag-overlap bonus that the conformance check uses too

The router returns a ranked list. The Mesh runs conformance on the top
candidate and falls through to the next if it fails.

Beyond the numbers, each candidate also carries the *audit features*
that produced its score -- the task tokens that matched the skill text,
and the tags that overlapped. These are what the explanation surface
shows so a human can see why a number is what it is.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import math

from .embeddings import EmbedFn, TfidfEmbedder, cosine, sparse_cosine, tokenize
from .graph import CapabilityGraph


@dataclass(frozen=True)
class RouteCandidate:
    agent: str
    skill_id: str
    score: float
    semantic: float
    tag_overlap: float
    matched_tokens: List[str] = field(default_factory=list)
    matched_tags: List[str] = field(default_factory=list)
    rejected_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "skill_id": self.skill_id,
            "score": round(self.score, 4),
            "semantic": round(self.semantic, 4),
            "tag_overlap": round(self.tag_overlap, 4),
            "matched_tokens": list(self.matched_tokens),
            "matched_tags": list(self.matched_tags),
            "rejected_reason": self.rejected_reason,
        }


class Router:
    """Resolves tasks to (agent, skill) pairs against a CapabilityGraph."""

    def __init__(
        self,
        graph: CapabilityGraph,
        *,
        embed_fn: Optional[EmbedFn] = None,
        tag_weight: float = 0.25,
    ) -> None:
        self.graph = graph
        self._embed_fn = embed_fn
        self._tag_weight = tag_weight
        self._skill_index: List[Tuple[str, str, List[str], str]] = []
        self._skill_vectors: List[List[float]] = []
        # Precomputed per-skill scoring aux (perf fix, task 3-4):
        # (sparse_vec, norm, skill_text_token_set, lowered_tag_set).
        self._skill_aux: List[Tuple[dict, float, set, set]] = []
        # Size-1 memo of the last route() — the deterministic router makes
        # this safe, and it collapses the L1-pre-route + dispatch-route
        # pair into ONE scoring pass. Cleared on any index change.
        self._route_cache: Optional[Tuple[tuple, List[RouteCandidate]]] = None
        self._builtin = TfidfEmbedder()
        self._stale = True
        # Serializes index rebuild so two concurrent route() calls cannot
        # both enter _rebuild_index and produce inconsistent in-place state.
        # The lock is uncontended in the steady state -- it's only acquired
        # on the rebuild path.
        self._rebuild_lock = threading.Lock()

    # ---- index management ---------------------------------------------
    def mark_stale(self) -> None:
        self._stale = True
        self._route_cache = None

    def _rebuild_index(self) -> None:
        """Rebuild the skill-index in place.

        Caller must hold `_rebuild_lock`. We compute the full new state
        into locals first, then publish via two paired attribute writes
        so a concurrent reader that already captured `_skill_index` /
        `_skill_vectors` continues to see a consistent pre-swap pair.
        """
        new_index: List[Tuple[str, str, List[str], str]] = []
        corpus: List[str] = []
        for node in self.graph.agents():
            for s in node.card.skills:
                text = " ".join(
                    [
                        node.card.description,
                        s.search_text(),
                    ]
                ).strip()
                new_index.append((node.name, s.id, list(s.tags), text))
                corpus.append(text)
        if not new_index:
            new_vectors: List[List[float]] = []
        elif self._embed_fn is not None:
            new_vectors = list(self._embed_fn(corpus))
        else:
            self._builtin.fit(corpus)
            new_vectors = self._builtin.embed(corpus)
        new_aux: List[Tuple[dict, float, set, set]] = []
        for (agent, skill_id, tags, text), vec in zip(new_index, new_vectors):
            sparse = {i: v for i, v in enumerate(vec) if v}
            norm = math.sqrt(sum(v * v for v in sparse.values()))
            new_aux.append((sparse, norm, set(tokenize(text)),
                            {t.lower() for t in tags}))
        # Publish atomically: index, vectors and aux are always paired.
        self._skill_index = new_index
        self._skill_vectors = new_vectors
        self._skill_aux = new_aux
        self._route_cache = None
        self._stale = False

    def _embed_query(self, text: str) -> List[float]:
        if self._embed_fn is not None:
            return list(self._embed_fn([text])[0])
        return self._builtin.embed_one(text)

    # ---- routing -------------------------------------------------------
    def route(
        self,
        task: str,
        *,
        tags: Optional[Sequence[str]] = None,
        top_k: int = 5,
    ) -> List[RouteCandidate]:
        cache_key = (task, tuple(tags or ()), top_k)
        if not self._stale:
            cached = self._route_cache
            if cached is not None and cached[0] == cache_key:
                return list(cached[1])
        if self._stale:
            # Double-checked locking: only the first concurrent caller pays
            # the rebuild cost; the rest wait on the lock and see _stale=False
            # when they re-check.
            with self._rebuild_lock:
                if self._stale:
                    self._rebuild_index()
        # Capture local references so a concurrent mark_stale() + rebuild
        # cannot swap the index out from under this call mid-loop.
        skill_index = self._skill_index
        skill_aux = self._skill_aux
        if not skill_index:
            return []
        query_vec = self._embed_query(task)
        q_sparse = {i: v for i, v in enumerate(query_vec) if v}
        q_norm = math.sqrt(sum(v * v for v in q_sparse.values()))
        query_tags = {t.lower() for t in (tags or [])}
        # Cheap tag tokens harvested from the task itself so users can route
        # without explicitly tagging.
        query_tokens = set(tokenize(task))

        scored: List[RouteCandidate] = []
        for (agent, skill_id, _skill_tags, _skill_text), \
                (sparse, norm, skill_text_tokens, tag_set) in zip(
                    skill_index, skill_aux):
            sem = sparse_cosine(q_sparse, q_norm, sparse, norm)
            overlap = 0.0
            if tag_set:
                explicit = len(tag_set & query_tags) / len(tag_set)
                implicit = len(tag_set & query_tokens) / len(tag_set)
                overlap = max(explicit, implicit * 0.5)
            score = (1.0 - self._tag_weight) * sem + self._tag_weight * overlap

            # Audit fields: which task tokens appear in the skill text,
            # which task/skill tags overlap. Precomputed at rebuild,
            # invaluable for the explanation surface.
            matched_tokens = sorted(query_tokens & skill_text_tokens)
            matched_tags = sorted(tag_set & (query_tags | query_tokens))

            scored.append(
                RouteCandidate(
                    agent=agent,
                    skill_id=skill_id,
                    score=score,
                    semantic=sem,
                    tag_overlap=overlap,
                    matched_tokens=matched_tokens,
                    matched_tags=matched_tags,
                )
            )
        scored.sort(key=lambda c: c.score, reverse=True)
        result = scored[:top_k]
        self._route_cache = (cache_key, list(result))
        return result
