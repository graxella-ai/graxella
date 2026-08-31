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

import ast
import logging
import re
from pathlib import Path

from mnema.integrations.sdk import MnemaClient

_log = logging.getLogger("graxella")

from graxella.beliefs.records import (OBSERVED_CONFIDENCE, RECALL_MAX_CHARS,
                                      OutcomeRecord, RecalledCase,
                                      is_outcome_statement, render_recall_tiered)

# Matches the canonical decision-statement rendering produced by
# record_decision: "[delegate] task='...' chose='...' :: rationale".
_DECISION_RE = re.compile(r"^\[\w+\] task=('.*?'|\".*?\") chose=", re.DOTALL)


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
    # Task 3-1: durable write buffer — takes the two-per-dispatch SQLite
    # writes off the hot path (WAL append + background flush; reads
    # drain first; crash recovery on startup).
    buffered: bool = False
    _client: MnemaClient | None = field(default=None, init=False, repr=False)
    _buffer: Any | None = field(default=None, init=False, repr=False)
    _tracer_hooks: list[TracerHook] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = MnemaClient(
            db_path=self.db_path,
            agent_id=self.agent_id,
            namespace=self.namespace,
            llm=self.llm,
            embedder=self.embedder,
        )
        if self.buffered:
            from graxella.beliefs.buffer import WalBuffer
            self._buffer = WalBuffer(
                self._client, Path(self.db_path).with_suffix(".wal.jsonl"))

    def _observe(self, statement: str, **kwargs: Any) -> str:
        """Write path: buffered when enabled, direct otherwise."""
        if self._buffer is not None:
            return self._buffer.observe(statement, **kwargs)
        return self._client.observe(statement, **kwargs)

    def _sync(self) -> None:
        """Read barrier: never read a ledger behind your own writes."""
        if self._buffer is not None:
            self._buffer.drain()

    # -- construction helpers ------------------------------------------------

    @classmethod
    def sqlite(cls, db_path: str, *, agent_id: str, namespace: str = "default",
               llm: Any | None = None, embedder: Any | None = None,
               buffered: bool = False) -> "Memory":
        """Shortcut: SQLite-backed memory for one agent."""
        return cls(agent_id=agent_id, db_path=db_path, namespace=namespace,
                   llm=llm, embedder=embedder, buffered=buffered)

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
        aid = self._observe(
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
                       err_class: str | None = None,
                       session_id: str | None = None,
                       tools_used: list[str] | None = None) -> str:
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
            session_id=session_id,
            tools_used=list(tools_used)[:10] if tools_used else None,
        )
        aid = self._observe(
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

    # -- governance signals (task 1-7) ---------------------------------------

    def record_signal(self, *, kind: str, decision_id: str,
                      detail: dict) -> str:
        """Persist one detection-only governance signal (e.g. a
        reasoning–action mismatch), linked to the decision that raised
        it. Miners read these back via ``signals(kind=...)``."""
        import json as _json
        aid = self._observe(
            _json.dumps({"kind": kind, "decision_id": decision_id,
                         **detail}, sort_keys=True, default=str),
            subject=decision_id,
            predicate="signal",
            object=kind,
            confidence=OBSERVED_CONFIDENCE,
            source_id="detector",
            derived_from=(decision_id,),
        )
        self._emit("signal", {"assertion_id": aid, "kind": kind,
                              "decision_id": decision_id, **detail})
        return aid

    def signals(self, *, kind: str | None = None) -> list[dict]:
        """All governance signals, optionally filtered by kind. Each row
        carries the assertion id plus the parsed detail."""
        import json as _json
        out = []
        for row in self.beliefs(predicate="signal"):
            if kind is not None and row["object"] != kind:
                continue
            detail = _json.loads(row["statement"])
            out.append({"assertion_id": row["id"], **detail})
        return out

    # -- forward provenance: touched edges -----------------------------------
    # derived_from answers "what evidence led to this?" (backward). A touched
    # edge answers "what did this decision affect?" (forward) -- so provenance
    # is a graph you can walk both ways, and impact/retraction is precise.

    def record_touch(self, decision_id: str, target: str, *,
                     role: str = "entity", detail: dict | None = None) -> str:
        """Record that ``decision_id`` TOUCHED (created / affected) ``target``.

        ``role`` classes the edge (``entity`` for a business object like
        ``order:1234``, ``proposal``/``rule`` for a governance artifact,
        ``tool`` for a capability). The edge is ``derived_from`` the decision,
        so retracting the decision cascades to it. Returns the assertion id.
        """
        import json as _json
        stmt = _json.dumps({"role": role, "target": target,
                            "decision_id": decision_id, **(detail or {})},
                           sort_keys=True, default=str)
        aid = self._observe(stmt, subject=decision_id, predicate="touched",
                            object=str(target), confidence=OBSERVED_CONFIDENCE,
                            source_id="orchestrator",
                            derived_from=(decision_id,))
        self._emit("touched", {"assertion_id": aid, "decision_id": decision_id,
                               "target": str(target), "role": role})
        return aid

    def touched_by(self, decision_id: str) -> list[dict]:
        """Forward provenance: everything ``decision_id`` affected."""
        import json as _json
        return [{"assertion_id": r["id"], **_json.loads(r["statement"])}
                for r in self.beliefs(subject=decision_id, predicate="touched")]

    def touching(self, target: str) -> list[dict]:
        """Reverse provenance: every decision that touched ``target`` -- the
        audit query "show me everything that happened to this entity",
        across every agent, from one ledger."""
        import json as _json
        out: list[dict] = []
        for r in self.beliefs(predicate="touched"):
            if r["object"] != str(target):
                continue
            out.append({"assertion_id": r["id"], "decision_id": r["subject"],
                        **_json.loads(r["statement"])})
        return out

    def provenance(self, assertion_id: str) -> dict:
        """One assertion's full provenance, both directions: the backward
        evidence it was ``derived_from`` and the forward artifacts it
        ``touched``. Turns ``why()`` from a lookup into a walkable graph."""
        why = self.why(assertion_id)
        prov = (why.get("provenance") or {}) if isinstance(why, dict) else {}
        return {
            "assertion_id": assertion_id,
            "assertion": why.get("assertion") if isinstance(why, dict) else None,
            "derived_from": list(prov.get("derived_from") or []),
            "touched": self.touched_by(assertion_id),
        }

    # -- case recall (task 0B-1) ---------------------------------------------

    def similar_cases(self, task: str, *, top_k: int = 3,
                      domain: str | None = None,
                      min_similarity: float = 0.05) -> list[RecalledCase]:
        """Memento pattern: the top-k most similar past decisions with
        their observed outcomes. Decisions without a recorded outcome are
        skipped — recall only serves verified experience.
        """
        # Over-fetch: the search corpus mixes decisions with outcome JSON
        # and other beliefs; we filter to decision assertions afterward.
        self._sync()
        hits = self._client.search_assertions(task, top_k=max(top_k * 4, 12))
        cases: list[RecalledCase] = []
        for score, a in hits:
            if a.get("predicate") != "decision" or score < min_similarity:
                continue
            parsed_task = _parse_decision_task(a.get("statement") or "")
            if parsed_task is None:
                continue
            outcomes = self.outcomes_for(a["id"])
            if not outcomes:
                continue
            latest = outcomes[-1]
            if domain is not None and latest.domain != domain:
                continue
            cases.append(RecalledCase(
                similarity=round(float(score), 4),
                task=parsed_task,
                chosen=a.get("object") or (latest.chosen or "?"),
                ok=latest.ok,
                completion=latest.completion,
                err=latest.err,
            ))
            if len(cases) >= top_k:
                break
        return cases

    def recall(self, task: str, *, top_k: int = 6, detail_k: int = 1,
               domain: str | None = None, min_similarity: float = 0.05,
               max_chars: int = RECALL_MAX_CHARS) -> str:
        """Tiered recall block, ready to inject as dispatch context.

        Fetches a BROAD candidate set (``top_k``) of verified similar cases,
        then renders it progressively: the ``detail_k`` most relevant in full
        (L2), the rest as one-line headlines (L0), all within ``max_chars``.
        Breadth without the token cost of dumping every case in full -- the
        agent sees that N similar tasks exist and reads only the ones that
        matter. Returns "" when there is no verified prior experience.
        """
        cases = self.similar_cases(task, top_k=top_k, domain=domain,
                                   min_similarity=min_similarity)
        return render_recall_tiered(cases, max_chars=max_chars,
                                    detail_k=detail_k)

    # -- typed read-side (task 0A-1 / 0A-3) ----------------------------------

    def beliefs(self, *, subject: str | None = None,
                predicate: str | None = None) -> list[dict]:
        """Typed-query passthrough to the ledger (used by the Evidence
        Gate and tests). Each row: id, subject, predicate, object,
        statement, confidence, derived_from, asserted_at."""
        self._sync()
        return self._client.beliefs(subject=subject, predicate=predicate)

    def outcomes_for(self, decision_id: str) -> list[OutcomeRecord]:
        """All typed outcomes recorded against one decision."""
        rows = self.beliefs(subject=decision_id, predicate="outcome")
        return [OutcomeRecord.from_statement(r["statement"]) for r in rows
                if is_outcome_statement(r["statement"])]

    def outcome_stats(self, *, domain: str | None = None) -> dict:
        """Aggregate the outcome ledger — the value-ledger v0 (task 0A-3).

        Every number here is computed from ledger assertions alone; there
        is no side-channel bookkeeping to drift out of sync.
        """
        rows = self.beliefs(predicate="outcome")
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
        self._sync()
        return self._client.search(query, top_k=top_k)

    def inject(self, *, max_chars: int = 2000) -> str:
        return self._client.inject(max_chars=max_chars)

    def why(self, assertion_id: str) -> dict:
        self._sync()
        return self._client.why(assertion_id)

    def timeline(self, subject: str) -> list[dict]:
        self._sync()
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
            except Exception as exc:
                # Tracer hooks must never break memory writes — but a
                # broken hook is an observability outage; say so (0C-3).
                _log.warning("graxella: memory tracer hook failed on %r: "
                             "%s: %s", event_type, type(exc).__name__, exc)


def _parse_decision_task(statement: str) -> str | None:
    """Recover the task text from a canonical decision statement."""
    m = _DECISION_RE.match(statement)
    if m is None:
        return None
    try:
        return str(ast.literal_eval(m.group(1)))
    except (ValueError, SyntaxError):
        return None


def best_embedder() -> Any:
    """The richest locally-available embedder for semantic recall.

    Prefers dense semantic vectors (a local embedding model such as
    nomic-embed-text via Ollama) and falls back to a lexical embedder only
    when no model is reachable. This is what ``Memory`` uses by default when
    no ``embedder=`` is passed; exposed here so callers can name or inspect
    the active embedder WITHOUT importing mnema internals -- graxella's
    surface stays the only import a graxella user needs.

    Name it with ``getattr(best_embedder(), 'model_id', ...)``.
    """
    from mnema.integrations.sdk import _best_embedder
    return _best_embedder()


def embedder_id(embedder: Any) -> str:
    """A human-readable name for an embedder (its model id, else its type)."""
    return getattr(embedder, "model_id", type(embedder).__name__)
