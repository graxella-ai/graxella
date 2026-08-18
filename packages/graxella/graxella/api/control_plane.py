"""graxella.api.control_plane — the control-plane service (task 3-1).

The Phase 3 split: the DATA plane (mesh, dispatch, healing, WAL buffer)
runs embedded in the customer's process with no network on the hot path;
this service is the CONTROL plane — review, audit, stats, and the
ingestion target a remote data plane pushes its WAL batches to.

Availability contract: the control plane being down never affects
dispatch — data planes buffer locally (graxella.beliefs.buffer) and
sync when it returns.

Endpoints (all read/write the ledger, nothing else):
  GET  /health                 liveness + ledger counts
  GET  /stats                  outcome/token accounting (0A-3)
  GET  /trust                  cited tool trust scores (2-6)
  GET  /gate/pending           review queue from the ledger (1-8)
  GET  /gate/why/{proposal_id} rendered verdict with citations
  POST /gate/approve           {proposal_id, by, note}
  POST /gate/reject            {proposal_id, by, note}
  POST /ingest                 [{statement, subject, predicate, ...}]
                               — WAL-batch ingestion from data planes

Run:  uvicorn --factory 'graxella.api.control_plane:make_app'  (or mount
``create_app(memory)`` in any ASGI host). Requires graxella[api].
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from graxella.beliefs.adapter import Memory
from graxella.gate.evidence import EvidenceGate, pending_from_ledger


class Decision(BaseModel):
    proposal_id: str
    by: str
    note: str = ""


class IngestEntry(BaseModel):
    statement: str
    assertion_id: Optional[str] = None
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    confidence: Optional[float] = None
    source_id: str = "data-plane"
    derived_from: list[str] = []


def create_app(memory: Memory, *, gate: EvidenceGate | None = None) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:
        raise ImportError(
            "the control plane needs fastapi — install graxella[api]"
        ) from exc

    gate = gate or EvidenceGate(memory)
    app = FastAPI(title="graxella control plane", version="0.1")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok",
                "beliefs": len(memory.beliefs()),
                "namespace": memory.namespace}

    @app.get("/stats")
    def stats(domain: str | None = None) -> dict:
        return memory.outcome_stats(domain=domain)

    @app.get("/trust")
    def trust(domain: str | None = None) -> dict:
        from axon_fabric.trust import tool_trust
        return {name: t.model_dump()
                for name, t in tool_trust(memory, domain=domain).items()}

    @app.get("/gate/pending")
    def gate_pending() -> list[dict]:
        return pending_from_ledger(memory)

    @app.get("/gate/why/{proposal_id}")
    def gate_why(proposal_id: str) -> dict:
        return {"proposal_id": proposal_id, "why": gate.why(proposal_id)}

    @app.post("/gate/approve")
    def gate_approve(d: Decision) -> dict:
        aid = gate.approve(d.proposal_id, by=d.by, note=d.note)
        return {"recorded": aid}

    @app.post("/gate/reject")
    def gate_reject(d: Decision) -> dict:
        aid = gate.reject(d.proposal_id, by=d.by, note=d.note)
        return {"recorded": aid}

    @app.post("/ingest")
    def ingest(entries: list[IngestEntry]) -> dict:
        applied = []
        for e in entries:
            kwargs = e.model_dump(exclude={"statement"}, exclude_none=True)
            kwargs["derived_from"] = tuple(kwargs.pop("derived_from", ()))
            applied.append(memory._client.observe(e.statement, **kwargs))
        return {"applied": len(applied), "assertion_ids": applied}

    return app


def make_app() -> Any:
    """uvicorn factory: serves the default project workdir ledger."""
    return create_app(Memory.sqlite(".graxella/mnema.db",
                                    agent_id="graxella-mesh"))


__all__ = ["create_app", "make_app"]
