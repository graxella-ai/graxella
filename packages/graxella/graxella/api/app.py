"""graxella.api.app — FastAPI surface for the operator dashboard.

Beat 1.5 lands the full CRUD surface plus a zero-build single-page HTML
dashboard served at ``/dashboard`` (or ``/`` if the root is unclaimed).
Everything is namespaced under a small number of route groups so the same
JSON contracts can back a proper React UI later without churn.
"""
from __future__ import annotations

from typing import Any

from graxella.api.dashboard import DASHBOARD_HTML

try:
    from pydantic import BaseModel  # type: ignore

    class ActorBody(BaseModel):
        by: str = "anonymous"
        note: str = ""
except ImportError:  # pragma: no cover
    ActorBody = None  # type: ignore


def create_app(*, tracer: Any | None = None,
               memory: Any | None = None,
               society: Any | None = None,
               gate: Any | None = None,
               constitution: Any | None = None) -> Any:
    """Return a FastAPI app wired to the runtime primitives. Import of
    FastAPI is lazy so graxella works without it installed."""
    try:
        from fastapi import FastAPI, HTTPException  # type: ignore
        from fastapi.responses import HTMLResponse  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "graxella.api requires FastAPI. Install with: pip install fastapi uvicorn"
        ) from exc

    app = FastAPI(
        title="graxella runtime",
        version="0.1.5",
        description=("Operator API for an instrumented graxella runtime. "
                     "Read + approve/reject/activate + dashboard."),
    )

    # ---------------- health ------------------------------------------------

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "ok": True,
            "components": {
                "tracer": tracer is not None,
                "memory": memory is not None,
                "society": society is not None,
                "gate": gate is not None,
                "constitution": constitution is not None,
            },
        }

    # ---------------- tracer ------------------------------------------------

    @app.get("/tracer/events")
    def tracer_events(limit: int = 100, source: str | None = None,
                      event_type: str | None = None) -> list[dict]:
        if tracer is None:
            return []
        evts = tracer.events(source=source, event_type=event_type, limit=limit)
        return [
            {"seq": e.seq, "ts": e.ts, "source": e.source,
             "event_type": e.event_type, "payload": e.payload}
            for e in evts
        ]

    @app.get("/tracer/why/{assertion_id}")
    def tracer_why(assertion_id: str) -> dict:
        if tracer is None:
            return {"error": "tracer not attached"}
        return tracer.why(assertion_id, memory=memory)

    # ---------------- gate --------------------------------------------------

    def _proposal_dict(p: Any) -> dict:
        return {
            "id": p.id, "kind": p.kind, "score": p.score,
            "blast_radius": p.blast_radius, "status": p.status.value,
            "payload": p.payload, "created_at": p.created_at,
            "decided_at": p.decided_at, "decided_by": p.decided_by,
            "note": p.note,
            "objectives": p.objectives.to_dict() if p.objectives else None,
        }

    @app.get("/gate/proposals")
    def gate_proposals(status: str | None = None) -> list[dict]:
        if gate is None:
            return []
        rows = [_proposal_dict(p) for p in gate.all()]
        if status:
            rows = [r for r in rows if r["status"] == status]
        return sorted(rows, key=lambda r: r["id"])

    @app.get("/gate/proposals/{proposal_id}")
    def gate_proposal(proposal_id: int) -> dict:
        if gate is None:
            raise HTTPException(404, "gate not attached")
        try:
            return _proposal_dict(gate.get(proposal_id))
        except KeyError:
            raise HTTPException(404, f"proposal {proposal_id} not found")

    @app.get("/gate/pending")
    def gate_pending() -> list[dict]:
        if gate is None:
            return []
        return [_proposal_dict(p) for p in gate.pending()]

    @app.post("/gate/proposals/{proposal_id}/approve")
    def gate_approve(proposal_id: int, body: ActorBody) -> dict:
        if gate is None:
            raise HTTPException(404, "gate not attached")
        try:
            return _proposal_dict(gate.approve(proposal_id, by=body.by, note=body.note))
        except KeyError:
            raise HTTPException(404, f"proposal {proposal_id} not found")

    @app.post("/gate/proposals/{proposal_id}/reject")
    def gate_reject(proposal_id: int, body: ActorBody) -> dict:
        if gate is None:
            raise HTTPException(404, "gate not attached")
        try:
            return _proposal_dict(gate.reject(proposal_id, by=body.by, note=body.note))
        except KeyError:
            raise HTTPException(404, f"proposal {proposal_id} not found")

    @app.post("/gate/proposals/{proposal_id}/activate")
    def gate_activate(proposal_id: int, body: ActorBody) -> dict:
        if gate is None:
            raise HTTPException(404, "gate not attached")
        try:
            return _proposal_dict(gate.activate(proposal_id, by=body.by))
        except KeyError:
            raise HTTPException(404, f"proposal {proposal_id} not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/gate/proposals/{proposal_id}/auto_evaluate")
    def gate_auto_evaluate(proposal_id: int, body: ActorBody) -> dict:
        if gate is None:
            raise HTTPException(404, "gate not attached")
        try:
            decision, p = gate.auto_evaluate(proposal_id, by=body.by)
            return {"decision": decision.value, "proposal": _proposal_dict(p)}
        except KeyError:
            raise HTTPException(404, f"proposal {proposal_id} not found")

    # ---------------- constitution -----------------------------------------

    @app.get("/constitution")
    def constitution_current() -> dict:
        if constitution is None:
            return {"version": "0", "document": {}}
        return {"version": constitution.version, "document": constitution.document}

    @app.get("/constitution/violations")
    def constitution_violations(limit: int = 200) -> list[dict]:
        if tracer is None:
            return []
        evts = tracer.events(event_type="governance.constitution_violation", limit=limit)
        return [{"seq": e.seq, "ts": e.ts, **e.payload} for e in evts]

    # ---------------- society ----------------------------------------------

    @app.get("/society/agents")
    def society_agents() -> list[dict]:
        if society is None:
            return []
        return society.describe()

    @app.get("/topology/graph")
    def topology_graph() -> dict:
        if society is None:
            return {"nodes": [], "edges": []}

        nodes: list[dict] = []
        edges: list[dict] = []
        edge_calls: dict[tuple[str, str], int] = {}

        for agent in society.describe():
            agent_id = f"agent:{agent['name']}"
            nodes.append({
                "id": agent_id, "type": "agent", "label": agent["name"],
                "skills": [s["name"] for s in agent.get("skills", [])],
            })
            for skill in agent.get("skills", []):
                skill_id = f"skill:{agent['name']}:{skill['id']}"
                nodes.append({
                    "id": skill_id, "type": "skill",
                    "label": skill["name"], "tags": skill.get("tags", []),
                })
                key = (agent_id, skill_id)
                edges.append({"source": agent_id, "target": skill_id,
                              "kind": "has_skill", "calls": 0})
                edge_calls[key] = 0

        if tracer is not None:
            for e in tracer.events(source="society", event_type="route"):
                p = e.payload or {}
                a, s = p.get("chosen_agent"), p.get("chosen_skill")
                if a and s:
                    key = (f"agent:{a}", f"skill:{a}:{s}")
                    if key in edge_calls:
                        edge_calls[key] += 1

        for edge in edges:
            edge["calls"] = edge_calls.get((edge["source"], edge["target"]), 0)

        return {"nodes": nodes, "edges": edges}

    # ---------------- dashboard --------------------------------------------

    @app.get("/dashboard", response_class=HTMLResponse)
    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return DASHBOARD_HTML

    return app
