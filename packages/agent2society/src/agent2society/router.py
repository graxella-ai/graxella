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

from .embeddings import EmbedFn, TfidfEmbedder, cosine, tokenize
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
        # Publish atomically: index and vectors are always paired.
        self._skill_index = new_index
        self._skill_vectors = new_vectors
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
        skill_vectors = self._skill_vectors
        if not skill_index:
            return []
        query_vec = self._embed_query(task)
        query_tags = {t.lower() for t in (tags or [])}
        # Cheap tag tokens harvested from the task itself so users can route
        # without explicitly tagging.
        query_tokens_list = tokenize(task)
        query_tokens = set(query_tokens_list)

        scored: List[RouteCandidate] = []
        for (agent, skill_id, skill_tags, skill_text), vec in zip(
            skill_index, skill_vectors
        ):
            sem = cosine(query_vec, vec)
            tag_set = {t.lower() for t in skill_tags}
            overlap = 0.0
            if tag_set:
                explicit = len(tag_set & query_tags) / len(tag_set)
                implicit = len(tag_set & query_tokens) / len(tag_set)
                overlap = max(explicit, implicit * 0.5)
            score = (1.0 - self._tag_weight) * sem + self._tag_weight * overlap

            # Audit fields: which task tokens appear in the skill text,
            # which task/skill tags overlap. Cheap to compute, invaluable
            # for the explanation surface.
            skill_text_tokens = set(tokenize(skill_text))
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
        return scored[:top_k]
