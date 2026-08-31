"""graxella.api.pipeline_compile — the graph → runnable pipeline link.

Closes the authoring loop: the visual manifest (nodes + edges) plus each
node's notebook code compile into a runnable pipeline that executes in
topological order and records a governed decision + outcome per node — so a
run lights up the very same topology and traces the UI already shows.

Two outputs:
  * run_pipeline(...)  — execute now, in-process, against the live ledger.
  * compile_to_py(...) — emit a standalone generate.py artifact (code is the
    source of truth; the canvas is its view).
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable


def _safe(node_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", node_id) or "node"


def _topo(nodes: list[dict], edges: list[dict]) -> list[str]:
    ids = [n["id"] for n in nodes]
    idset = set(ids)
    adj: dict[str, list[str]] = {i: [] for i in ids}
    indeg: dict[str, int] = {i: 0 for i in ids}
    for e in edges:
        if e.get("from") in idset and e.get("to") in idset:
            adj[e["from"]].append(e["to"])
            indeg[e["to"]] += 1
    q = deque([i for i in ids if indeg[i] == 0])
    order: list[str] = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    for i in ids:  # any cycle remnants, in declared order
        if i not in order:
            order.append(i)
    return order


def _run_cell_source(notebook_dir: Path, node_id: str) -> str | None:
    """The `def run(...)` body from a node's notebook, or None."""
    p = notebook_dir / f"{_safe(node_id)}.ipynb"
    if not p.exists():
        return None
    try:
        nb = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        src = "".join(cell.get("source", []))
        if "def run(" in src:
            lines = src[src.index("def run("):].splitlines()
            out = [lines[0]]
            for ln in lines[1:]:
                if ln and not ln[0].isspace():  # dedent to col 0 ends the fn
                    break
                out.append(ln)
            return "\n".join(out)
    return None


def extract_node_fn(notebook_dir: Path, node_id: str) -> Callable | None:
    """Exec a node's notebook code cells and return its `run` callable."""
    p = notebook_dir / f"{_safe(node_id)}.ipynb"
    if not p.exists():
        return None
    try:
        nb = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    ns: dict[str, Any] = {}
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        try:
            exec("".join(cell.get("source", [])), ns)  # noqa: S102 (local dev)
        except Exception:
            pass
        if callable(ns.get("run")):
            break
    fn = ns.get("run")
    return fn if callable(fn) else None


def _kind_to_decision(kind: str) -> str:
    return "delegate" if kind == "agent" else "tool"


def run_pipeline(manifest: dict, notebook_dir: Path, memory: Any,
                 *, payload: dict | None = None) -> dict:
    """Execute the pipeline once against `memory`, recording governed
    decision + outcome per node. Returns a run summary."""
    nodes = manifest.get("nodes", [])
    edges = manifest.get("edges", [])
    order = _topo(nodes, edges)
    kind = {n["id"]: n.get("kind", "tool") for n in nodes}
    fns = {n["id"]: (extract_node_fn(notebook_dir, n["id"]) or (lambda p: p))
           for n in nodes}
    preds: dict[str, list[str]] = {}
    for e in edges:
        preds.setdefault(e.get("to"), []).append(e.get("from"))
    domain = manifest.get("name") or "pipeline"
    outputs: dict[str, dict] = {}
    executed: list[dict] = []
    base = payload or {"input": "demo run"}

    for nid in order:
        inp: dict = {}
        for pid in preds.get(nid, []):
            o = outputs.get(pid)
            if isinstance(o, dict):
                inp.update(o)
        if not inp:
            inp = dict(base)
        k = _kind_to_decision(kind.get(nid, "tool"))
        t = time.perf_counter()
        aid = memory.record_decision(decision_type=k, task=nid, chosen=nid,
                                     domain=domain)
        ok, err, out = True, None, inp
        try:
            out = fns[nid](dict(inp))
        except Exception as exc:  # a node error is a recorded outcome, not a crash
            ok, err = False, str(exc)
        dt = (time.perf_counter() - t) * 1000
        memory.record_outcome(
            decision_id=aid, ok=ok, kind=k, chosen=nid, domain=domain,
            latency_ms=round(dt, 2), err=err,
            err_class=("drift" if err and "deprecat" in err.lower() else None),
            session_id="run")
        outputs[nid] = out if isinstance(out, dict) else {"result": out}
        executed.append({"node": nid, "ok": ok, "ms": round(dt, 2), "err": err})

    return {"ran": True, "domain": domain, "nodes": len(order),
            "executed": executed,
            "failed": [e["node"] for e in executed if not e["ok"]]}


def compile_to_py(manifest: dict, notebook_dir: Path, pkg_root: Path,
                  out_path: Path, *, db: str) -> Path:
    """Emit a standalone runnable generate.py for this pipeline."""
    nodes = manifest.get("nodes", [])
    edges = manifest.get("edges", [])
    order = _topo(nodes, edges)
    name = manifest.get("name") or "pipeline"

    parts: list[str] = []
    parts.append(f'"""Generated by graxella from pipeline {name!r}.')
    parts.append("Edit each node's logic in graxella_nodes/<id>.ipynb, then "
                 "recompile.")
    parts.append('"""')
    parts.append("from __future__ import annotations")
    parts.append("import sys, time")
    parts.append(f"sys.path.insert(0, r{str(pkg_root)!r})")
    parts.append("from graxella.beliefs import Memory")
    parts.append("")
    parts.append("# ------------------------------------------------ nodes")
    fn_names: dict[str, str] = {}
    for n in nodes:
        nid = n["id"]
        fname = f"node_{_safe(nid)}"
        fn_names[nid] = fname
        body = _run_cell_source(notebook_dir, nid)
        if body:
            parts.append(body.replace("def run(", f"def {fname}(", 1))
        else:
            parts.append(f"def {fname}(payload):")
            parts.append(f"    # TODO: implement in graxella_nodes/{_safe(nid)}"
                         ".ipynb")
            parts.append("    return payload")
        parts.append("")
    parts.append("# ------------------------------------------------ wiring")
    parts.append("NODES = {" + ", ".join(f"{n['id']!r}: {fn_names[n['id']]}"
                                          for n in nodes) + "}")
    parts.append("KIND = " + repr({n["id"]: n.get("kind", "tool")
                                   for n in nodes}))
    parts.append("EDGES = " + repr([(e["from"], e["to"]) for e in edges
                                    if "from" in e and "to" in e]))
    parts.append("ORDER = " + repr(order))
    parts.append(f"DOMAIN = {name!r}")
    parts.append(f"DB = r{db!r}")
    parts.append("")
    parts.append('''def run_pipeline(payload=None):
    m = Memory.sqlite(DB, agent_id="graxella-mesh")
    outputs, base = {}, payload or {"input": "run"}
    for nid in ORDER:
        preds = [f for (f, t) in EDGES if t == nid]
        inp = {}
        for pid in preds:
            o = outputs.get(pid)
            if isinstance(o, dict):
                inp.update(o)
        if not inp:
            inp = dict(base)
        k = "delegate" if KIND.get(nid) == "agent" else "tool"
        t0 = time.perf_counter()
        aid = m.record_decision(decision_type=k, task=nid, chosen=nid, domain=DOMAIN)
        ok, err, out = True, None, inp
        try:
            out = NODES[nid](dict(inp))
        except Exception as exc:
            ok, err = False, str(exc)
        dt = (time.perf_counter() - t0) * 1000
        m.record_outcome(decision_id=aid, ok=ok, kind=k, chosen=nid, domain=DOMAIN,
                         latency_ms=round(dt, 2), err=err,
                         err_class=("drift" if err and "deprecat" in err.lower() else None),
                         session_id="run")
        outputs[nid] = out if isinstance(out, dict) else {"result": out}
        print(f"  {'ok ' if ok else 'ERR'} {nid} ({dt:.1f}ms)")
    return outputs


if __name__ == "__main__":
    print(f"running pipeline {DOMAIN!r} ...")
    run_pipeline()
    print("done — open the graxella UI to see topology + traces update")''')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return out_path


__all__ = ["run_pipeline", "compile_to_py", "extract_node_fn"]
