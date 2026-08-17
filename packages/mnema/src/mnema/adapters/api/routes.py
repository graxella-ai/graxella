"""FastAPI compliance API for Mnema.

Exposes the audit/compliance surface as HTTP endpoints.
Designed for AI compliance officers and automated audit pipelines.

Mount: app = FastAPI(); app.include_router(router, prefix="/mnema")
"""

from __future__ import annotations

import os
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Security
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from pydantic import BaseModel as PydanticBaseModel
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    APIRouter = object  # type: ignore[assignment, misc]
    HTTPException = Exception  # type: ignore[assignment, misc]
    PydanticBaseModel = object  # type: ignore[assignment, misc]
    Depends = Security = HTTPBearer = HTTPAuthorizationCredentials = None  # type: ignore[assignment]

from mnema.adapters.sqlite.repository import SqliteMnemaStore
from mnema.config import settings
from mnema.services.auditor import AuditService
from mnema.services.consolidator import SleepConsolidator
from mnema.services.graph import KnowledgeGraph
from mnema.services.recorder import MemoryRecorder
from mnema.services.retriever import MemoryRetriever

_BEARER = HTTPBearer(auto_error=False) if _FASTAPI_AVAILABLE else None


def _make_auth_dep() -> Any:
    """Return a FastAPI dependency that validates Bearer token against MNEMA_API_KEY env var.

    If MNEMA_API_KEY is not set the API is open (development mode).
    Set the env var in production to require authentication on all mutating routes.
    """
    if not _FASTAPI_AVAILABLE:
        return None

    required_key = os.environ.get("MNEMA_API_KEY", "")

    def _check(
        credentials: HTTPAuthorizationCredentials | None = Security(_BEARER),  # noqa: B008
    ) -> None:
        if not required_key:
            return  # no key configured — open in dev mode
        if credentials is None or credentials.credentials != required_key:
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key. Set Authorization: Bearer <MNEMA_API_KEY>",
            )

    return _check


def build_router(db_url: str = "sqlite:///mnema.db") -> Any:
    """Build and return the FastAPI router. Raises ImportError if fastapi not installed."""
    if not _FASTAPI_AVAILABLE:
        raise ImportError("fastapi is required: pip install fastapi uvicorn")

    store = SqliteMnemaStore(db_url)
    auth = _make_auth_dep()
    router = APIRouter(tags=["mnema"])

    # ── Request / Response models ──────────────────────────────────────────────

    class ObserveRequest(PydanticBaseModel):  # type: ignore[misc]
        agent_id: str
        statement: str
        subject: str | None = None
        confidence: float | None = None
        namespace: str | None = None

    class ReviseRequest(PydanticBaseModel):  # type: ignore[misc]
        statement: str
        confidence: float | None = None

    # ── Belief endpoints ───────────────────────────────────────────────────────

    @router.post("/observe", dependencies=[Depends(auth)])
    def observe(req: ObserveRequest) -> dict:
        rec = MemoryRecorder(store)
        assertion = rec.observe(
            req.agent_id, req.statement,
            subject=req.subject,
            confidence=req.confidence,
            namespace=req.namespace,
        )
        return {"assertion_id": assertion.id, "status": "recorded"}

    @router.post("/assertions/{assertion_id}/revise", dependencies=[Depends(auth)])
    def revise(assertion_id: str, req: ReviseRequest) -> dict:
        rec = MemoryRecorder(store)
        try:
            revised = rec.revise(assertion_id, statement=req.statement,
                                 confidence=req.confidence)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"assertion_id": revised.id, "supersedes": revised.supersedes}

    @router.post("/assertions/{assertion_id}/retract", dependencies=[Depends(auth)])
    def retract(assertion_id: str) -> dict:
        rec = MemoryRecorder(store)
        try:
            rec.retract(assertion_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"assertion_id": assertion_id, "status": "retracted"}

    @router.post("/assertions/{assertion_id}/cascade-retract", dependencies=[Depends(auth)])
    def cascade_retract(assertion_id: str) -> dict:
        rec = MemoryRecorder(store)
        try:
            affected = rec.cascade_retract(assertion_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"retracted": affected[0], "at_risk_artifacts": affected[1:]}

    # ── Retrieval endpoints ────────────────────────────────────────────────────

    @router.get("/agents/{agent_id}/inject")
    def inject(agent_id: str, max_chars: int = Query(default=settings.digest_max_chars)) -> dict:
        ret = MemoryRetriever(store)
        return {"agent_id": agent_id, "prompt_block": ret.render(agent_id, max_chars=max_chars)}

    @router.get("/agents/{agent_id}/beliefs")
    def beliefs(agent_id: str, subject: str | None = Query(default=None)) -> dict:
        ret = MemoryRetriever(store)
        items = ret.search(agent_id, subject) if subject else []
        count = ret.belief_count(agent_id)
        return {"agent_id": agent_id, "count": count, "beliefs": [b.id for b in items]}

    @router.get("/agents/{agent_id}/search")
    def semantic_search(
        agent_id: str,
        q: str = Query(..., description="Free-text query"),
        top_k: int = Query(default=5),
    ) -> dict:
        """Rank all active beliefs by semantic similarity to the query text."""
        from mnema.integrations.sdk import _best_embedder
        ret = MemoryRetriever(store, embedder=_best_embedder())
        ranked = ret.semantic_search(agent_id, q, top_k=top_k)
        return {
            "agent_id": agent_id,
            "query": q,
            "results": [
                {"score": round(score, 4), "assertion_id": b.id, "statement": b.statement}
                for score, b in ranked
            ],
        }

    # ── Audit / compliance endpoints ───────────────────────────────────────────

    @router.get("/assertions/{assertion_id}/why")
    def why_believed(assertion_id: str) -> dict:
        result = AuditService(store).why_believed(assertion_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    @router.get("/agents/{agent_id}/timeline")
    def timeline(agent_id: str, subject: str = Query(...)) -> list:
        return AuditService(store).belief_timeline(agent_id, subject)

    @router.get("/agents/{agent_id}/report")
    def compliance_report(agent_id: str) -> dict:
        return AuditService(store).compliance_report(agent_id)

    @router.get("/agents/{agent_id}/digest/diff")
    def digest_diff(agent_id: str, from_v: int = Query(...), to_v: int = Query(...)) -> dict:
        result = AuditService(store).digest_diff(agent_id, from_v, to_v)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    # ── Graph / contradiction endpoints ───────────────────────────────────────

    class ContradictRequest(PydanticBaseModel):  # type: ignore[misc]
        from_id: str
        to_id: str
        reason: str
        confidence: float = 0.9

    class ResolveRequest(PydanticBaseModel):  # type: ignore[misc]
        note: str

    @router.post("/agents/{agent_id}/contradict", dependencies=[Depends(auth)])
    def assert_contradiction(agent_id: str, req: ContradictRequest) -> dict:
        """Declare that two assertions contradict each other."""
        kg = KnowledgeGraph(store)
        try:
            rel = kg.assert_contradicts(
                agent_id, req.from_id, req.to_id,
                reason=req.reason, confidence=req.confidence,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"relationship_id": rel.id, "status": rel.status}

    @router.get("/agents/{agent_id}/contradictions")
    def get_contradictions(agent_id: str) -> dict:
        """Return all open contradictions for an agent with statement text."""
        kg = KnowledgeGraph(store)
        items = kg.open_contradictions(agent_id)
        return {
            "agent_id": agent_id,
            "open_count": len(items),
            "contradictions": items,
        }

    @router.post("/relationships/{rel_id}/resolve", dependencies=[Depends(auth)])
    def resolve_relationship(rel_id: str, req: ResolveRequest) -> dict:
        """Resolve a contradiction — record the decision and reason."""
        kg = KnowledgeGraph(store)
        try:
            rel = kg.resolve(rel_id, note=req.note)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"relationship_id": rel.id, "status": rel.status, "resolution_note": req.note}

    @router.post("/agents/{agent_id}/consolidate", dependencies=[Depends(auth)])
    def consolidate(agent_id: str, scenario: str = Query(default="")) -> dict:
        from mnema.adapters.llm.fake import FakeLLM
        from mnema.adapters.llm.ollama import OllamaLLM
        llm = FakeLLM.from_scenario(scenario) if scenario else OllamaLLM()
        digest = SleepConsolidator(store, llm).consolidate(agent_id)
        if digest is None:
            return {"status": "skipped", "reason": "fewer than 2 beliefs or LLM error"}
        return {"status": "ok", "digest_version": digest.version,
                "rules": len(digest.rules), "skills": len(digest.skills)}

    return router
