"""graxella.api.ui — the local governance UI and the ``graxella serve`` command.

`graxella serve --db ./ledger.db` boots the control-plane API, adds a
pipeline-flow endpoint with live health, serves a dependency-free single-page
UI at ``/``, and opens the browser.

`graxella serve --demo` needs no ledger: it seeds an in-memory demo pipeline
(shaped like a Notion → format → LinkedIn flow) with one tool whose behavior
changed and one proposal awaiting review — so the view is populated on the
first run.

The Pipeline view renders an authored pipeline as a left-to-right flow of
node cards grouped into stages, each card colored by live health from the
ledger. The pipeline *structure* comes from a manifest (``pipeline.json``
beside the ledger, or the mesh in Stage 3); the *health* comes from the
ledger. The Inbox view reads/writes only the existing control-plane gate
endpoints.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from pathlib import Path
from typing import Any

from graxella.beliefs.adapter import Memory
from graxella.gate.evidence import EvidenceGate

_STATIC = Path(__file__).parent / "static"


# --------------------------------------------------------------- demo pipeline
# A manifest is a flat, positioned graph:
#   {"name", "nodes":[{"id","label","subtitle","kind","x","y","group"}],
#    "edges":[{"from","to"}]}
# kind is one of NODE_KINDS. x/y are canvas pixels; group is a backdrop label.
NODE_KINDS = ["trigger", "tool", "agent", "skill", "gate", "memory"]

DEMO_MANIFEST: dict[str, Any] = {
    "name": "daily-linkedin-post",
    "nodes": [
        {"id": "trigger", "label": "On schedule", "subtitle": "daily 09:00",
         "kind": "trigger", "x": 40, "y": 210, "group": "Trigger"},
        {"id": "DB_DATE_Filter", "label": "DB_DATE_Filter",
         "subtitle": "getAll: databasePage", "kind": "tool",
         "x": 250, "y": 150, "group": "Fetch from Notion"},
        {"id": "Get_All_Blocks", "label": "Get_All_Blocks",
         "subtitle": "getAll: block", "kind": "tool",
         "x": 250, "y": 260, "group": "Fetch from Notion"},
        {"id": "Aggregate", "label": "Aggregate blocks",
         "subtitle": "merge notion blocks", "kind": "tool",
         "x": 480, "y": 150, "group": "Process & format"},
        {"id": "AI_Agent", "label": "AI Agent", "subtitle": "tools agent · llm",
         "kind": "agent", "x": 480, "y": 260, "group": "Process & format"},
        {"id": "SizeCheck", "label": "SizeCheck", "subtitle": "gate / branch",
         "kind": "gate", "x": 720, "y": 110, "group": "Posting on LinkedIn"},
        {"id": "Truncation", "label": "Truncation", "subtitle": "format",
         "kind": "skill", "x": 720, "y": 205, "group": "Posting on LinkedIn"},
        {"id": "Publish_Short", "label": "Publish (shortened)",
         "subtitle": "LinkedIn: post", "kind": "tool",
         "x": 720, "y": 300, "group": "Posting on LinkedIn"},
        {"id": "Publish_Full", "label": "Publish (full)",
         "subtitle": "LinkedIn: post", "kind": "tool",
         "x": 720, "y": 395, "group": "Posting on LinkedIn"},
    ],
    "edges": [
        {"from": "trigger", "to": "DB_DATE_Filter"},
        {"from": "DB_DATE_Filter", "to": "Get_All_Blocks"},
        {"from": "Get_All_Blocks", "to": "Aggregate"},
        {"from": "Aggregate", "to": "AI_Agent"},
        {"from": "AI_Agent", "to": "SizeCheck"},
        {"from": "SizeCheck", "to": "Truncation"},
        {"from": "Truncation", "to": "Publish_Short"},
        {"from": "SizeCheck", "to": "Publish_Full"},
    ],
}


def _health_map(memory: Memory) -> dict[str, str]:
    """Per-node health from the ledger: 'amber' if any drift/failure,
    'healthy' if it has clean outcomes, absent if unseen."""
    from graxella.beliefs.records import OutcomeRecord, is_outcome_statement

    agg: dict[str, dict[str, int]] = {}
    for row in memory.beliefs(predicate="outcome"):
        stmt = row["statement"]
        if not is_outcome_statement(stmt):
            continue
        rec = OutcomeRecord.from_statement(stmt)
        key = (rec.chosen or "?").split("::")[0]
        a = agg.setdefault(key, {"ok": 0, "bad": 0})
        if rec.ok and rec.err_class != "drift":
            a["ok"] += 1
        else:
            a["bad"] += 1
    health: dict[str, str] = {}
    for key, a in agg.items():
        health[key] = "amber" if a["bad"] else "healthy"
    return health


def _pipeline_payload(memory: Memory, manifest: dict | None) -> dict:
    if not manifest:
        return {"stages": [], "edges": [], "health": {}, "name": None}
    return {**manifest, "health": _health_map(memory)}


def _seed_demo(memory: Memory) -> None:
    """Populate a ledger so the demo pipeline shows real health + one proposal.

    Get_All_Blocks is the tool whose behavior changed (amber); everything
    else is healthy. One transform proposal for it awaits review.
    """
    from graxella.healing.recipes import TransformRecipe

    lat = {"DB_DATE_Filter": 120, "Aggregate": 45, "SizeCheck": 8,
           "Truncation": 22, "Publish_Short": 260, "Publish_Full": 240}
    for tool, ms in lat.items():
        for i in range(6):
            aid = memory.record_decision(decision_type="tool", task=f"{tool}-{i}",
                                         chosen=tool, domain="linkedin")
            memory.record_outcome(decision_id=aid, ok=True, kind="tool",
                                  chosen=tool, domain="linkedin",
                                  latency_ms=ms + i * 3, session_id=f"s{i % 3}")
    # the agent — healthy, with token usage
    for i in range(8):
        aid = memory.record_decision(decision_type="delegate", task=f"post-{i}",
                                     chosen="AI_Agent", domain="linkedin")
        memory.record_outcome(decision_id=aid, ok=True, kind="delegate",
                              chosen="AI_Agent", domain="linkedin",
                              latency_ms=1400 + i * 40, tokens_in=820,
                              tokens_out=190, session_id=f"s{i % 3}")
    # Get_All_Blocks — behavior changed (drift)
    for i in range(4):
        aid = memory.record_decision(decision_type="tool", task=f"blocks-{i}",
                                     chosen="Get_All_Blocks", domain="linkedin")
        memory.record_outcome(decision_id=aid, ok=False, kind="tool",
                              chosen="Get_All_Blocks", domain="linkedin",
                              latency_ms=90 + i * 5,
                              err="unknown field 'block_id' schema deprecated",
                              err_class="drift", session_id=f"s{i % 3}")
    gate = EvidenceGate(memory)
    prop = TransformRecipe(field_map={"block_id": "id"}).to_proposal(
        domain="linkedin", tool="Get_All_Blocks", origin="healer:demo")
    gate.refresh()
    gate.decide(prop)


def _load_manifest(db_path: str) -> dict | None:
    """A pipeline.json beside the ledger (or in its .graxella dir) if present."""
    p = Path(db_path)
    for cand in (p.with_suffix(".pipeline.json"), p.parent / "pipeline.json"):
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


#: the importable root of the graxella source tree (…/packages/graxella,
#: the dir that contains the ``graxella/`` package)
_PKG_ROOT = Path(__file__).resolve().parents[2]


def _find_node(manifest: dict | None, node_id: str) -> dict | None:
    for n in (manifest or {}).get("nodes", []):
        if n.get("id") == node_id:
            return n
    return None


def _traces_from_memory(memory: Memory, *, limit: int = 60) -> list[dict]:
    """Reconstruct OTel spans from the outcome ledger, using the same
    gen_ai.* / graxella.* schema graxella.otelbridge emits. These are the
    spans exported to an OTel collector / Jaeger — shown here in-app."""
    from graxella.beliefs.records import OutcomeRecord, is_outcome_statement

    spans: list[dict] = []
    for row in memory.beliefs(predicate="outcome"):
        stmt = row["statement"]
        if not is_outcome_statement(stmt):
            continue
        rec = OutcomeRecord.from_statement(stmt)
        chosen = (rec.chosen or "?").split("::")[0]
        op = "invoke_agent" if rec.kind in ("delegate", "agent") else rec.kind
        attrs = {
            "gen_ai.operation.name": op,
            "gen_ai.agent.name": chosen if op == "invoke_agent" else None,
            "gen_ai.usage.input_tokens": rec.tokens_in,
            "gen_ai.usage.output_tokens": rec.tokens_out,
            "graxella.domain": rec.domain,
            "graxella.ok": rec.ok,
            "graxella.err_class": rec.err_class,
        }
        spans.append({
            "name": f"{op} {chosen}",
            "node": chosen,
            "duration_ms": rec.latency_ms,
            "status": "OK" if (rec.ok and rec.err_class != "drift") else "ERROR",
            "ts": row.get("asserted_at"),
            "attributes": {k: v for k, v in attrs.items() if v is not None},
        })
    return spans[-limit:][::-1]


def build_app(memory: Memory, *, gate: EvidenceGate | None = None,
              manifest: dict | None = None,
              notebook_dir: Path | None = None,
              notebook_launcher: str = "auto",
              db_path: str | None = None) -> Any:
    """Control-plane app + pipeline-flow endpoint + topology + node notebooks
    + the UI page."""
    from fastapi import HTTPException
    from fastapi.responses import HTMLResponse

    from graxella.api.control_plane import create_app

    gate = gate or EvidenceGate(memory)
    app = create_app(memory, gate=gate)
    nb_dir = notebook_dir or (Path.cwd() / "graxella_nodes")
    # mutable holder so the editor can save a new manifest at runtime
    state: dict[str, Any] = {"manifest": manifest}
    manifest_path = nb_dir.parent / "pipeline.json"

    @app.get("/pipeline")
    def pipeline() -> dict:  # noqa: ANN202
        return _pipeline_payload(memory, state["manifest"])

    @app.get("/node_kinds")
    def node_kinds() -> list[str]:  # noqa: ANN202
        return NODE_KINDS

    @app.post("/pipeline/save")
    def pipeline_save(m: dict) -> dict:  # noqa: ANN202
        state["manifest"] = m
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(m, indent=2), encoding="utf-8")
            saved = str(manifest_path)
        except Exception as exc:  # keep runtime edit even if disk write fails
            saved = f"(in-memory only: {exc})"
        return {"ok": True, "saved_to": saved,
                "nodes": len(m.get("nodes", [])), "edges": len(m.get("edges", []))}

    @app.get("/traces")
    def traces() -> dict:  # noqa: ANN202
        return {"spans": _traces_from_memory(memory)}

    @app.post("/pipeline/run")
    def pipeline_run() -> dict:  # noqa: ANN202
        from graxella.api.pipeline_compile import run_pipeline
        m = state["manifest"]
        if not m or not m.get("nodes"):
            raise HTTPException(status_code=400, detail="no pipeline to run")
        try:
            return run_pipeline(m, nb_dir, memory)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/pipeline/compile")
    def pipeline_compile() -> dict:  # noqa: ANN202
        from graxella.api.pipeline_compile import compile_to_py
        from graxella.api.studio import open_node  # reuse the launcher pref
        m = state["manifest"]
        if not m or not m.get("nodes"):
            raise HTTPException(status_code=400, detail="no pipeline to compile")
        out = nb_dir.parent / "generate.py"
        ledger = db_path or str(nb_dir.parent / "mnema.db")
        try:
            compile_to_py(m, nb_dir, _PKG_ROOT, out, db=ledger)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        # best-effort open in the developer's editor
        opened = False
        try:
            from graxella.api.studio import find_code
            import subprocess
            exe = find_code()
            if exe and notebook_launcher in ("auto", "code"):
                subprocess.Popen([exe, "-r", str(out)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                opened = True
        except Exception:
            pass
        return {"ok": True, "path": str(out), "opened": opened}

    @app.post("/node/{node_id}/open")
    def node_open(node_id: str) -> dict:  # noqa: ANN202
        from graxella.api.studio import open_node
        m = state["manifest"]
        node = _find_node(m, node_id) or {"id": node_id, "kind": "tool"}
        try:
            return open_node(nb_dir, _PKG_ROOT, node, (m or {}).get("name"),
                             prefer=notebook_launcher)
        except Exception as exc:  # never 500 the UI over an editor launch
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/studio/jupyter")
    def jupyter_info() -> dict:  # noqa: ANN202
        from graxella.api.studio import JupyterServer
        return JupyterServer.get().info()

    @app.post("/studio/jupyter/start")
    def jupyter_start() -> dict:  # noqa: ANN202
        from graxella.api.studio import JupyterServer
        return JupyterServer.get().start(nb_dir.parent)

    @app.get("/topology")
    def topology(domain: str | None = None) -> dict:  # noqa: ANN202
        try:
            return topology_data_from_memory(memory, domain=domain)
        except Exception:
            return {"nodes": [], "edges": [], "domain": domain}

    index_html = (_STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:  # noqa: ANN202
        return index_html

    return app


def topology_data_from_memory(memory: Memory, *, domain: str | None = None) -> dict:
    """Ledger-only mesh graph (agents + tools + cited trust), for the
    fallback topology view when no pipeline manifest is present."""
    from graxella.beliefs.records import OutcomeRecord, is_outcome_statement
    from graxella.healing.trust import tool_trust

    routes: dict[str, int] = {}
    ok: dict[str, int] = {}
    tool_fail: dict[str, int] = {}
    for row in memory.beliefs(predicate="outcome"):
        stmt = row["statement"]
        if not is_outcome_statement(stmt):
            continue
        rec = OutcomeRecord.from_statement(stmt)
        chosen = rec.chosen or "?"
        if rec.kind in ("tool", "transform"):
            if (not rec.ok) or rec.err_class == "drift":
                tool_fail[chosen] = tool_fail.get(chosen, 0) + 1
        else:
            agent = chosen.split("::")[0]
            routes[agent] = routes.get(agent, 0) + 1
            if rec.ok:
                ok[agent] = ok.get(agent, 0) + 1
    nodes: list[dict[str, Any]] = []
    for agent, n in sorted(routes.items()):
        nodes.append({"id": agent, "kind": "agent", "routes": n,
                      "ok_rate": round(ok.get(agent, 0) / n, 3) if n else None})
    for tool, t in tool_trust(memory, domain=domain).items():
        amber = bool(tool_fail.get(tool)) or (t.score is not None and t.score < 0.6)
        nodes.append({"id": tool, "kind": "tool", "trust": t.score, "n": t.n,
                      "health": "amber" if amber else "healthy",
                      "fails": tool_fail.get(tool, 0)})
    return {"domain": domain, "nodes": nodes}


def serve(db: str = ".graxella/mnema.db", *, host: str = "127.0.0.1",
          port: int = 8756, agent: str = "graxella-mesh",
          namespace: str = "default", open_browser: bool = True,
          demo: bool = False, notebook_launcher: str = "auto") -> None:
    """Launch the governance UI against a ledger. Blocks until Ctrl-C.

    With demo=True, ignores db and serves a seeded in-memory demo pipeline.
    """
    import tempfile

    import uvicorn

    if demo:
        db = str(Path(tempfile.mkdtemp(prefix="graxella-demo-")) / "demo.db")
        memory = Memory.sqlite(db, agent_id=agent, namespace=namespace)
        _seed_demo(memory)
        manifest = DEMO_MANIFEST
        ledger_label = "DEMO (seeded in-memory)"
    else:
        memory = Memory.sqlite(db, agent_id=agent, namespace=namespace)
        manifest = _load_manifest(db)
        ledger_label = db

    nb_dir = Path(db).resolve().parent / "graxella_nodes"
    app = build_app(memory, manifest=manifest, notebook_dir=nb_dir,
                    notebook_launcher=notebook_launcher,
                    db_path=str(Path(db).resolve()))
    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(1.3, lambda: webbrowser.open(url)).start()
    print(f"\n  graxella governance UI  ->  {url}")
    print(f"  ledger: {ledger_label}")
    if manifest:
        print(f"  pipeline: {manifest.get('name', 'authored')} "
              f"({len(manifest.get('nodes', []))} nodes)")
    print("  Pipeline + Approval inbox.  Ctrl-C to stop.\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


__all__ = ["build_app", "serve", "topology_data_from_memory", "DEMO_MANIFEST"]
