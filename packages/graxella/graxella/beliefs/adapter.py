"""graxella.beliefs.adapter — thin wrap around the vendored MnemaClient.

Rationale for the wrap (rather than re-exporting MnemaClient directly):

1. The graxella surface stays stable even if mnema's SDK signature evolves.
2. Every write through this layer emits a matching graxella tracer event,
   so the unified tracer sees mnema writes without polling the WAL.
3. Domain-shaped helpers (``record_decision``, ``record_outcome``) hide
   the raw ``observe`` verb behind orchestration-layer semantics.

Everything else — retract cascade, why_believed, digest injection — is
forwarded to the underlying MnemaClient unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from mnema.integrations.sdk import MnemaClient

from graxella.beliefs.records import (OBSERVED_CONFIDENCE, OutcomeRecord,
                                      is_outcome_statement)


# Public tracer callback shape: (event_type, payload) -> None
TracerHook = Callable[[str, dict], None]


@dataclass
class Memory:
    """The belief / memory surface for an instrumented graph.

    Construct once per agent, pass into ``graxella.instrument(...)``. All
    orchestration decisions that flow through the wrapped graph become
    typed Assertions in the underlying mnema store, with provenance links
    back to the routing event that produced them.
    """

    agent_id: str
    db_path: str = "./graxella-mnema.db"
    namespace: str = "default"
    llm: Any | None = None
    embedder: Any | None = None
    _client: MnemaClient | None = field(default=None, init=False, repr=False)
    _tracer_hooks: list[TracerHook] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = MnemaClient(
            db_path=self.db_path,
            agent_id=self.agent_id,
            namespace=self.namespace,
            llm=self.llm,
            embedder=self.embedder,
        )

    # -- construction helpers ------------------------------------------------

    @classmethod
    def sqlite(cls, db_path: str, *, agent_id: str, namespace: str = "default",
               llm: Any | None = None) -> "Memory":
        """Shortcut: SQLite-backed memory for one agent."""
        return cls(agent_id=agent_id, db_path=db_path, namespace=namespace, llm=llm)

    def attach_tracer(self, hook: TracerHook) -> None:
        """Register a callback that fires on every write we perform."""
        self._tracer_hooks.append(hook)

    # -- orchestration-layer writes -----------------------------------------

    def record_decision(self, *,
                        decision_type: str,
                        task: str,
                        chosen: str,
                        rationale: str = "",
                        confidence: float | None = None,
                        domain: str | None = None,
                        model_id: str | None = None) -> str:
        """Persist one orchestration decision. Returns assertion_id.

        ``decision_type`` is one of: spawn | delegate | communicate |
        aggregate | stop.  ``chosen`` is the concrete choice made (e.g. the
        agent name for a delegate). The statement stays human-readable —
        it is the semantic-search surface for case recall — while the SPO
        triple (predicate="decision", object=chosen) gives typed filters.
        """
        statement = f"[{decision_type}] task={task!r} chose={chosen!r} :: {rationale}"
        subject = f"decision::{decision_type}::{chosen}"
        aid = self._client.observe(
            statement,
            subject=subject,
            predicate="decision",
            object=chosen,
            confidence=confidence,
            source_id="orchestrator",
        )
        self._emit("decision", {
            "assertion_id": aid,
            "decision_type": decision_type,
            "task": task,
            "chosen": chosen,
            "rationale": rationale,
            "confidence": confidence,
            "domain": domain or self.namespace,
            "model_id": model_id,
        })
        return aid

    def record_outcome(self, *,
                       decision_id: str,
                       ok: bool,
                       score: float | None = None,
                       err: str | None = None,
                       cost_tokens: int | None = None,
                       latency_ms: float | None = None,
                       tokens_in: int | None = None,
                       tokens_out: int | None = None,
                       cost_usd: float | None = None,
                       model_id: str | None = None,
                       domain: str | None = None,
                       kind: str = "delegate",
                       chosen: str | None = None,
                       violations: int = 0,
                       err_class: str | None = None) -> str:
        """Persist the observed outcome of a previously-recorded decision
        as a typed OutcomeRecord (task 0A-1). Returns the assertion_id.

        The link back to the decision is structural, twice over:
        ``subject`` IS the decision assertion id (query outcomes with
        ``beliefs(subject=decision_id)``) and provenance ``derived_from``
        carries it into the retraction cascade.

        Epistemics: observed outcomes — success or failure — are recorded
        at OBSERVED_CONFIDENCE. A watched failure is not an uncertain one.

        Legacy args: ``score`` maps to ``completion``; ``cost_tokens``
        maps to ``tokens_out`` when the split isn't known.
        """
        record = OutcomeRecord(
            decision_id=decision_id,
            ok=ok,
            domain=domain or self.namespace,
            kind=kind,
            chosen=chosen,
            model_id=model_id,
            completion=score,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out if tokens_out is not None else cost_tokens,
            cost_usd=cost_usd,
            violations=violations,
            err_class=err_class,
            err=(err or None) and str(err)[:500],
        )
        aid = self._client.observe(
            record.to_statement(),
            subject=decision_id,
            predicate="outcome",
            object="ok" if ok else "fail",
            confidence=OBSERVED_CONFIDENCE,
            source_id="orchestrator",
            derived_from=(decision_id,),
        )
        self._emit("outcome", {"assertion_id": aid, **record.model_dump(mode="json")})
        return aid

    # -- typed read-side (task 0A-1 / 0A-3) ----------------------------------

    def outcomes_for(self, decision_id: str) -> list[OutcomeRecord]:
        """All typed outcomes recorded against one decision."""
        rows = self._client.beliefs(subject=decision_id, predicate="outcome")
        return [OutcomeRecord.from_statement(r["statement"]) for r in rows
                if is_outcome_statement(r["statement"])]

    def outcome_stats(self, *, domain: str | None = None) -> dict:
        """Aggregate the outcome ledger — the value-ledger v0 (task 0A-3).

        Every number here is computed from ledger assertions alone; there
        is no side-channel bookkeeping to drift out of sync.
        """
        rows = self._client.beliefs(predicate="outcome")
        records = [OutcomeRecord.from_statement(r["statement"]) for r in rows
                   if is_outcome_statement(r["statement"])]
        if domain is not None:
            records = [r for r in records if r.domain == domain]

        def _agg(rs: list[OutcomeRecord]) -> dict:
            n = len(rs)
            oks = sum(1 for r in rs if r.ok)
            lat = [r.latency_ms for r in rs if r.latency_ms is not None]
            return {
                "count": n,
                "ok": oks,
                "ok_rate": round(oks / n, 4) if n else None,
                "tokens_in": sum(r.tokens_in or 0 for r in rs),
                "tokens_out": sum(r.tokens_out or 0 for r in rs),
                "cost_usd": round(sum(r.cost_usd or 0.0 for r in rs), 6),
                "avg_latency_ms": round(sum(lat) / len(lat), 2) if lat else None,
                "violations": sum(r.violations for r in rs),
            }

        by_domain: dict[str, list[OutcomeRecord]] = {}
        for r in records:
            by_domain.setdefault(r.domain, []).append(r)
        return {
            "total": _agg(records),
            "by_domain": {d: _agg(rs) for d, rs in sorted(by_domain.items())},
        }

    # -- read-side (forwarded) ----------------------------------------------

    def search(self, query: str, *, top_k: int = 5) -> list[tuple[float, str]]:
        return self._client.search(query, top_k=top_k)

    def inject(self, *, max_chars: int = 2000) -> str:
        return self._client.inject(max_chars=max_chars)

    def why(self, assertion_id: str) -> dict:
        return self._client.why(assertion_id)

    def timeline(self, subject: str) -> list[dict]:
        return self._client.timeline(subject)

    def report(self) -> dict:
        return self._client.report()

    def snapshot(self) -> dict:
        return self._client.snapshot()

    def retraction_cascade(self, assertion_id: str) -> list[str]:
        return self._client.retraction_cascade(assertion_id)

    def consolidate(self) -> Optional[Any]:
        return self._client.consolidate() if self.llm is not None else None

    # -- internal ------------------------------------------------------------

    def _emit(self, event_type: str, payload: dict) -> None:
        for hook in self._tracer_hooks:
            try:
                hook(event_type, payload)
            except Exception:
                # Tracer hooks must never break memory writes.
                pass
