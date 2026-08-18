from __future__ import annotations

from typing import Any

from mnema.adapters.sqlite.repository import SqliteMnemaStore
from mnema.config import settings
from mnema.core.consolidation import Digest
from mnema.core.graph import Relationship
from mnema.debug.inspector import BeliefInspector
from mnema.debug.replay import EventReplayer
from mnema.services.auditor import AuditService
from mnema.services.consolidator import SleepConsolidator
from mnema.services.graph import KnowledgeGraph
from mnema.services.recorder import MemoryRecorder
from mnema.services.retriever import MemoryRetriever


class MnemaClient:
    """Single-object entry point. Minimal integration: 3 lines to add Mnema to any agent."""

    def __init__(
        self,
        *,
        db_path: str = settings.db_url,
        agent_id: str = "default",
        namespace: str = settings.default_namespace,
        llm: Any | None = None,
        embedder: Any | None = None,
    ) -> None:
        # Any SQLAlchemy URL passes through untouched (postgresql+psycopg://
        # engages the Postgres backend — task 3-2); bare paths get the
        # sqlite default.
        url = db_path if "://" in db_path else f"sqlite:///{db_path}"
        self._store = SqliteMnemaStore(url)
        self.agent_id = agent_id
        self.namespace = namespace
        self._recorder = MemoryRecorder(self._store)
        self._retriever = MemoryRetriever(self._store, embedder=embedder or _best_embedder())
        self._auditor = AuditService(self._store)
        self._llm = llm
        if llm is not None:
            self._consolidator: SleepConsolidator | None = SleepConsolidator(self._store, llm)
        else:
            self._consolidator = None
        self.inspector = BeliefInspector(self._store)
        self.replayer = EventReplayer(self._store)
        self.graph = KnowledgeGraph(self._store)

    # -- core lifecycle -------------------------------------------------------

    def observe(
        self,
        statement: str,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,
        confidence: float | None = None,
        source_id: str = "agent",
        derived_from: tuple[str, ...] = (),
        assertion_id: str | None = None,
    ) -> str:
        """Record an observation. Returns assertion_id.

        ``predicate``/``object`` complete the SPO triple for typed queries;
        ``derived_from`` links this observation to the assertions it depends
        on (feeds the retraction cascade).
        """
        a = self._recorder.observe(
            self.agent_id,
            statement,
            subject=subject,
            predicate=predicate,
            object=object,
            confidence=confidence,
            source_id=source_id,
            namespace=self.namespace,
            derived_from=derived_from,
            assertion_id=assertion_id,
        )
        return a.id

    def revise(self, prior_id: str, statement: str, *, confidence: float | None = None) -> str:
        """Revise an existing belief. Returns new assertion_id."""
        a = self._recorder.revise(prior_id, statement=statement, confidence=confidence)
        return a.id

    def retract(self, assertion_id: str) -> str:
        """Retract a belief. Returns retracted assertion_id."""
        a = self._recorder.retract(assertion_id)
        return a.id

    def consolidate(self, *, max_rules: int = 10, max_skills: int = 5) -> Digest | None:
        """Run sleep-phase consolidation. Requires llm to be set."""
        if self._consolidator is None:
            raise RuntimeError("MnemaClient requires an llm= to consolidate")
        return self._consolidator.consolidate(
            self.agent_id,
            namespace=self.namespace,
            max_rules=max_rules,
            max_skills=max_skills,
        )

    def inject(self, *, max_chars: int = settings.digest_max_chars) -> str:
        """Return the rendered digest for prompt injection. Empty string if no digest yet."""
        return self._retriever.render(self.agent_id, namespace=self.namespace, max_chars=max_chars)

    def search(self, query_text: str, *, top_k: int = 5) -> list[tuple[float, str]]:
        """Semantic search: return top-k (score, statement) pairs ranked by relevance."""
        ranked = self._retriever.semantic_search(
            self.agent_id, query_text, namespace=self.namespace, top_k=top_k
        )
        return [(score, b.statement) for score, b in ranked]

    def search_assertions(self, query_text: str, *, top_k: int = 5) -> list[tuple[float, dict]]:
        """Semantic search returning full assertion metadata, not just statements.

        Each hit is (score, {id, subject, predicate, object, statement,
        confidence}) — enough to join outcomes back to decisions without a
        second lookup. Used by graxella's case recall.
        """
        ranked = self._retriever.semantic_search(
            self.agent_id, query_text, namespace=self.namespace, top_k=top_k
        )
        return [
            (score, {
                "id": b.id,
                "subject": b.subject,
                "predicate": b.predicate,
                "object": b.object,
                "statement": b.statement,
                "confidence": b.confidence.value,
            })
            for score, b in ranked
        ]

    def beliefs(self, *, subject: str | None = None,
                predicate: str | None = None) -> list[dict]:
        """Current (non-retracted, non-superseded) beliefs as dicts, optionally
        filtered by exact subject and/or predicate. Typed-query surface for
        graxella's outcome ledger."""
        rows = self._store.current_beliefs(
            namespace=self.namespace, agent_id=self.agent_id, subject=subject
        )
        if predicate is not None:
            rows = [b for b in rows if b.predicate == predicate]
        return [
            {
                "id": b.id,
                "subject": b.subject,
                "predicate": b.predicate,
                "object": b.object,
                "statement": b.statement,
                "confidence": b.confidence.value,
                "derived_from": list(b.provenance.derived_from),
                "asserted_at": b.asserted_at.isoformat(),
            }
            for b in rows
        ]

    # -- query / audit --------------------------------------------------------

    def why(self, assertion_id: str) -> dict:
        """Compliance query: why was this believed?"""
        return self._auditor.why_believed(assertion_id)

    def timeline(self, subject: str) -> list[dict]:
        """Belief history for a subject."""
        return self._auditor.belief_timeline(self.agent_id, subject, namespace=self.namespace)

    def diff(self, v1: int, v2: int) -> dict:
        """What changed between digest version v1 and v2?"""
        return self._auditor.digest_diff(self.agent_id, v1, v2, namespace=self.namespace)

    def report(self) -> dict:
        """Full compliance report."""
        return self._auditor.compliance_report(self.agent_id, namespace=self.namespace)

    def snapshot(self) -> dict:
        """Debug snapshot of current memory state."""
        return self.inspector.snapshot(self.agent_id, namespace=self.namespace)

    def retraction_cascade(self, assertion_id: str) -> list[str]:
        """Which rules/skills are at risk if this belief is retracted?"""
        return self._recorder.retraction_cascade(assertion_id)

    # -- graph / contradiction workflow ----------------------------------------

    def assert_contradicts(
        self,
        from_id: str,
        to_id: str,
        *,
        reason: str,
        confidence: float = 0.9,
    ) -> Relationship:
        """Declare that two assertions contradict each other."""
        return self.graph.assert_contradicts(
            self.agent_id, from_id, to_id,
            reason=reason, confidence=confidence, namespace=self.namespace,
        )

    def open_contradictions(self) -> list[dict]:
        """Surface all unresolved contradictions for this agent."""
        return self.graph.open_contradictions(self.agent_id, namespace=self.namespace)

    def resolve_contradiction(self, relationship_id: str, *, note: str) -> Relationship:
        """Resolve a contradiction — record who decided and why."""
        return self.graph.resolve(relationship_id, note=note)

    def contradiction_summary(self) -> dict:
        """Quick integrity check: clean or conflicts_present?"""
        return self.graph.contradiction_summary(self.agent_id, namespace=self.namespace)

    # -- context manager ------------------------------------------------------

    def __enter__(self) -> MnemaClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass  # connection lifecycle managed by SqliteMnemaStore per-call


def _best_embedder() -> Any:
    """Auto-select the richest available embedder.

    Priority:
      1. OllamaEmbedder — dense semantic vectors (nomic-embed-text, 768-dim).
         Requires Ollama running locally. Fails fast and falls through if not reachable.
      2. SentenceTransformerEmbedder — local dense vectors, no server needed.
         Requires: pip install mnema[embed]  (blocked on Windows by Long Path limit).
      3. TfidfEmbedder — pure stdlib bag-of-words, always available.
         Good enough for keyword overlap; misses paraphrase and semantic drift.
    """
    from mnema.adapters.embedder.ollama import OllamaEmbedder
    candidate = OllamaEmbedder()
    if candidate.health_check():
        return candidate

    try:
        from mnema.adapters.embedder.sentence_transformer import SentenceTransformerEmbedder
        return SentenceTransformerEmbedder()
    except ImportError:
        pass

    import logging

    from mnema.adapters.embedder.tfidf import TfidfEmbedder
    logging.getLogger(__name__).warning(
        "OllamaEmbedder: Ollama not reachable at %s. "
        "Falling back to TF-IDF — semantic search quality is reduced. "
        "Start Ollama and run: ollama pull %s",
        candidate._base_url,
        candidate.model_id,
    )
    return TfidfEmbedder()
