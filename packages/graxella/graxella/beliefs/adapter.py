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
                        confidence: float | None = None) -> str:
        """Persist one orchestration decision. Returns assertion_id.

        ``decision_type`` is one of: spawn | delegate | communicate |
        aggregate | stop.  ``chosen`` is the concrete choice made (e.g. the
        agent name for a delegate). The statement is a canonical rendering
        of "on TASK, decision_type CHOSEN because RATIONALE".
        """
        statement = f"[{decision_type}] task={task!r} chose={chosen!r} :: {rationale}"
        subject = f"decision::{decision_type}::{chosen}"
        aid = self._client.observe(
            statement,
            subject=subject,
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
        })
        return aid

    def record_outcome(self, *,
                       decision_id: str,
                       ok: bool,
                       score: float | None = None,
                       err: str | None = None,
                       cost_tokens: int | None = None,
                       latency_ms: float | None = None) -> str:
        """Persist the observed outcome of a previously-recorded decision.

        Returns the new outcome assertion_id. The link back to the decision
        is preserved via the belief store's provenance chain (source_id
        carries the decision assertion id).
        """
        statement = (f"outcome ok={ok} score={score} cost_tokens={cost_tokens} "
                     f"latency_ms={latency_ms} err={err!r}")
        subject = f"outcome::{decision_id}"
        aid = self._client.observe(
            statement,
            subject=subject,
            confidence=1.0 if ok else 0.5,
            source_id=decision_id,
        )
        self._emit("outcome", {
            "assertion_id": aid,
            "decision_id": decision_id,
            "ok": ok,
            "score": score,
            "err": err,
            "cost_tokens": cost_tokens,
            "latency_ms": latency_ms,
        })
        return aid

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
