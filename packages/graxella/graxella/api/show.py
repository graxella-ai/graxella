"""graxella.api.show -- `graxella show`: the governance ledger, visually.

One command, one page: the evidence a session has accumulated, rendered
the way the operator needs to read it -- verdict chips, the review queue
with operable approve/reject, the heal record, the routing audit, and an
entity audit box ("everything that touched order:1234").

This is deliberately the REVIEW surface, not an authoring canvas: you
author agents in code (LangChain/LangGraph); you come here to see what
they did, why the gate decided what it decided, and to sign off. Think
"the pull-request review screen", never "the IDE".

Reads a session workdir (mnema.db + rulebook.json) through the same
Session object the runtime uses, so approve/reject go through the REAL
Evidence Gate -- nothing here bypasses governance.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_show_app(session: Any):
    """FastAPI app over one Session's live governance objects."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI(title=f"graxella show -- {session.name}")
    mem = session.memory

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _PAGE.replace("__NAME__", session.name) \
                    .replace("__DB__", str(session.workdir))

    @app.get("/api/overview")
    def overview() -> dict:
        stats = mem.outcome_stats().get("total", {})
        heals = [r for r in mem.beliefs(predicate="outcome")
                 if '"kind": "transform"' in r["statement"]
                 or "'kind': 'transform'" in r["statement"]]
        rules = session.rulebook.all_rules()
        return {
            "name": session.name,
            "outcomes": stats.get("count", 0),
            "ok_rate": stats.get("ok_rate"),
            "violations": stats.get("violations", 0),
            "heals": len(heals),
            "pending": len(session.pending()),
            "rules_active": sum(1 for r in rules if r.status == "active"),
            "rules_demoted": sum(1 for r in rules if r.status != "active"),
        }

    @app.get("/api/rules")
    def rules() -> list[dict]:
        """The rulebook: what's learned, what's still active, and what
        was un-learned (with the cited evidence why)."""
        from graxella.gate.health import rule_health
        out = []
        for r in reversed(session.rulebook.all_rules()):
            h = rule_health(mem, r.id)
            out.append({
                "id": r.id, "status": r.status,
                "replace_skill": r.replace_skill, "with_skill": r.with_skill,
                "approved_by": r.approved_by,
                "uses_ok": h.successes, "uses_failed": h.failures,
                "demoted_by": r.demoted_by, "demoted_reason": r.demoted_reason,
            })
        return out

    @app.get("/api/pending")
    def pending() -> list[dict]:
        rows = session.pending()
        for r in rows:
            try:
                r["why"] = session.why(r["proposal_id"])
            except Exception:
                r["why"] = ""
        return rows

    @app.post("/api/decide")
    def decide(body: dict) -> dict:
        pid, action = body.get("proposal_id"), body.get("action")
        by = body.get("by") or "operator:show-ui"
        note = body.get("note") or ""
        if action == "approve":
            promoted = session.approve(pid, by=by, note=note)
            # Cross-process truth: the approval is durably recorded and is
            # honored at the owning session's next decide()/reconcile();
            # immediate promotion needs the proposal payload, which lives
            # in the running session, not the ledger.
            return {"ok": True, "pending": len(session.pending()),
                    "promoted": promoted is not None,
                    "note": ("promoted to the rulebook" if promoted is not None
                             else "approval recorded; the running session "
                                  "promotes it on its next decide/reconcile")}
        if action == "reject":
            session.reject(pid, by=by, note=note)
            return {"ok": True, "pending": len(session.pending()),
                    "note": "rejection recorded (always honored)"}
        return {"ok": False, "error": f"unknown action {action!r}"}

    @app.get("/api/verdicts")
    def verdicts(limit: int = 25) -> list[dict]:
        out = []
        for row in mem.beliefs(predicate="gate_verdict")[-limit:]:
            try:
                data = json.loads(row["statement"])
            except Exception:
                continue
            if data.get("type") == "human":
                out.append({"proposal_id": row["subject"],
                            "decision": data.get("decision"),
                            "by": data.get("by"), "human": True,
                            "note": data.get("note", "")})
            else:
                out.append({"proposal_id": row["subject"],
                            "decision": data.get("decision"),
                            "posterior": data.get("posterior"),
                            "threshold": data.get("threshold"),
                            "reason": data.get("reason", ""),
                            "kind": data.get("kind"),
                            "human": False})
        out.reverse()
        return out

    @app.get("/api/decisions")
    def decisions(limit: int = 30) -> list[dict]:
        rows = mem.beliefs(predicate="decision")[-limit:]
        out = []
        for r in rows:
            outcomes = mem.outcomes_for(r["id"])
            latest = outcomes[-1] if outcomes else None
            out.append({
                "id": r["id"], "chosen": r["object"],
                "statement": (r["statement"] or "")[:140],
                "ok": (latest.ok if latest else None),
                "kind": (latest.kind if latest else None),
                "latency_ms": (latest.latency_ms if latest else None),
            })
        out.reverse()
        return out

    @app.get("/api/touching")
    def touching(target: str) -> list[dict]:
        return mem.touching(target)

    return app


def _ledger_identity(db_path: Path) -> tuple[str | None, str | None]:
    """(namespace, agent_id) holding most assertions in a ledger. Mnema
    scopes reads by BOTH, so a show session must adopt the identity the
    ledger was written under or it reads an empty database."""
    import sqlite3
    try:
        con = sqlite3.connect(str(db_path))
        row = con.execute(
            "SELECT namespace, agent_id, COUNT(*) FROM assertions "
            "GROUP BY namespace, agent_id ORDER BY 3 DESC LIMIT 1").fetchone()
        con.close()
        return (row[0], row[1]) if row else (None, None)
    except Exception:
        return (None, None)


def show(workdir: str = ".graxella", name: str | None = None,
         host: str = "127.0.0.1", port: int = 8321,
         open_browser: bool = True) -> None:
    """Serve the governance page for a session workdir (blocking)."""
    import graxella

    root = Path(workdir)
    # accept either a session dir itself or a .graxella root holding them
    if (root / "mnema.db").exists():
        sess_dir = root
    else:
        candidates = sorted((d for d in root.glob("*/")
                             if (d / "mnema.db").exists()),
                            key=lambda d: (d / "mnema.db").stat().st_mtime)
        if not candidates:
            raise SystemExit(
                f"no session ledger found under {root} -- run something "
                "first (see tutorials/), or pass --workdir <session dir>")
        sess_dir = candidates[-1]
    # Adopt the ledger's own identity (namespace + agent_id) — opening
    # with a different one reads an empty database.
    ns, agent = _ledger_identity(sess_dir / "mnema.db")
    sess_name = name or agent or sess_dir.name
    session = graxella.Session(sess_name, workdir=sess_dir, namespace=ns)

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("graxella show needs uvicorn -- pip install "
                         "'graxella[api]'") from exc

    url = f"http://{host}:{port}/"
    print(f"graxella show -> {url}   (session: {sess_name}, "
          f"ledger: {sess_dir / 'mnema.db'})")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(build_show_app(session), host=host, port=port,
                log_level="warning")


# --------------------------------------------------------------------------
# The page. Single file, no build step, no CDN -- works offline. Visual
# identity follows the graxella problem brief: evidence-ledger styling,
# verdict chips, mono ledger data, evidence-green accent, light+dark.
# --------------------------------------------------------------------------
_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>graxella &middot; __NAME__</title>
<style>
:root{--paper:#F3F5F1;--surface:#FBFCFA;--ink:#17211C;--body:#39423D;
--muted:#69726B;--line:#DFE4DD;--accent:#1E6E45;--accent-ink:#12532F;
--accent-soft:#E6F0E9;--rev:#8A6212;--rev-bg:#F3ECDB;--rej:#9A3B2E;
--rej-bg:#F1E1DD;}
@media (prefers-color-scheme:dark){:root{--paper:#0E1411;--surface:#151D18;
--ink:#ECEFE9;--body:#BFC7C0;--muted:#859089;--line:#253029;--accent:#59B884;
--accent-ink:#8FD4AC;--accent-soft:#14231B;--rev:#C79A4E;--rev-bg:#241E12;
--rej:#D2846F;--rej-bg:#241614;}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--body);
font:15px/1.6 "Segoe UI",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.mono{font-family:Consolas,ui-monospace,monospace;font-variant-numeric:tabular-nums}
.wrap{max-width:1060px;margin:0 auto;padding:1.6rem 1.2rem 3rem}
header{display:flex;align-items:baseline;gap:.8rem;border-bottom:1px solid var(--line);
padding-bottom:1rem;margin-bottom:1.4rem;flex-wrap:wrap}
header h1{font-family:Georgia,serif;font-weight:500;font-size:1.35rem;color:var(--ink);margin:0}
header .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);
box-shadow:0 0 0 4px var(--accent-soft);align-self:center}
header .db{font-size:.72rem;color:var(--muted)}
h2{font-family:Georgia,serif;font-weight:500;font-size:1.05rem;color:var(--ink);
margin:1.6rem 0 .6rem;letter-spacing:.01em}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:.7rem}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:8px;
padding:.7rem .9rem}
.tile .n{font-family:Consolas,monospace;font-size:1.5rem;color:var(--ink)}
.tile .l{font-size:.68rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:8px;
padding:.85rem 1rem;margin-bottom:.7rem}
.chip{display:inline-block;font-family:Consolas,monospace;font-size:.66rem;
letter-spacing:.1em;text-transform:uppercase;padding:.22em .55em;border-radius:3px}
.chip.ap{background:var(--accent-soft);color:var(--accent-ink)}
.chip.rv{background:var(--rev-bg);color:var(--rev)}
.chip.rj{background:var(--rej-bg);color:var(--rej)}
pre.why{font-family:Consolas,monospace;font-size:.74rem;line-height:1.55;
color:var(--muted);background:var(--paper);border:1px solid var(--line);
border-radius:6px;padding:.6rem .8rem;overflow-x:auto;margin:.6rem 0}
button{font:inherit;font-size:.8rem;padding:.35rem .9rem;border-radius:6px;
border:1px solid var(--line);background:var(--surface);color:var(--ink);cursor:pointer}
button.ok{background:var(--accent);border-color:var(--accent);color:#fff}
button:hover{filter:brightness(1.06)}
table{width:100%;border-collapse:collapse;font-size:.8rem}
td,th{padding:.35rem .5rem;border-bottom:1px solid var(--line);text-align:left}
th{font-size:.66rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
.okmk{color:var(--accent-ink)} .bad{color:var(--rej)}
input{font:inherit;padding:.4rem .7rem;border:1px solid var(--line);
border-radius:6px;background:var(--surface);color:var(--ink);min-width:230px}
.empty{color:var(--muted);font-size:.85rem;font-style:italic}
footer{margin-top:2rem;border-top:1px solid var(--line);padding-top:.8rem;
font-size:.7rem;color:var(--muted)}
</style></head><body><div class="wrap">
<header><span class="dot"></span><h1>graxella &middot; __NAME__</h1>
<span class="db mono">__DB__</span></header>

<div class="tiles" id="tiles"></div>

<h2>Review queue &mdash; changes awaiting a human</h2>
<div id="msg" style="font-size:.8rem;color:var(--accent-ink)"></div>
<div id="pending"><p class="empty">loading&hellip;</p></div>

<h2>Gate verdicts &mdash; every decision, cited</h2>
<div id="verdicts"></div>

<h2>Rulebook &mdash; what's learned, and what stopped earning its keep</h2>
<div id="rules"></div>

<h2>Recent decisions &mdash; the ledger</h2>
<table id="decisions"><thead><tr><th>ok</th><th>chosen</th><th>kind</th>
<th>ms</th><th>statement</th></tr></thead><tbody></tbody></table>

<h2>Entity audit &mdash; everything that touched&hellip;</h2>
<div class="card"><input id="q" placeholder="e.g. order:1234"
onkeydown="if(event.key==='Enter')audit()">
<button onclick="audit()">audit</button>
<div id="audit"></div></div>

<footer>the review surface, not an authoring canvas &mdash; agents are
authored in code; governance is signed off here. auto-refreshes.</footer>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function j(u,opt){const r=await fetch(u,opt);return r.json();}
function chip(d){const c=d==="auto_approve"?"ap":(d==="auto_reject"||d==="human_rejected")?"rj":"rv";
return `<span class="chip ${c}">${esc(d)}</span>`;}
async function refresh(){
 const o=await j("/api/overview");
 $("tiles").innerHTML=[["outcomes",o.outcomes],["ok rate",o.ok_rate==null?"-":o.ok_rate],
  ["heals",o.heals],["pending review",o.pending],["active rules",o.rules_active],
  ["un-learned",o.rules_demoted],["violations",o.violations]].map(([l,n])=>
  `<div class="tile"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></div>`).join("");
 const p=await j("/api/pending");
 $("pending").innerHTML=p.length?p.map(x=>`<div class="card">
   ${chip("needs_human")} <b>${esc(x.kind)}</b>
   <span class="mono" style="font-size:.72rem">${esc(x.proposal_id)}</span>
   <div style="margin:.3rem 0">${esc(x.reason||"")}</div>
   <pre class="why">${esc(x.why||"")}</pre>
   <button class="ok" onclick="decide('${esc(x.proposal_id)}','approve')">Approve</button>
   <button onclick="decide('${esc(x.proposal_id)}','reject')">Reject</button>
  </div>`).join(""):'<p class="empty">nothing awaiting review</p>';
 const ru=await j("/api/rules");
 $("rules").innerHTML=ru.length?("<table><thead><tr><th>status</th><th>rule</th>"+
  "<th>substitutes</th><th>uses ok/failed</th><th>note</th></tr></thead><tbody>"+
  ru.map(x=>`<tr><td>${x.status==="active"?chip("auto_approve"):chip("auto_reject")}</td>
   <td class="mono" style="font-size:.72rem">${esc(x.id)}</td>
   <td class="mono">${esc(x.replace_skill)}${x.with_skill?" &rarr; "+esc(x.with_skill):""}</td>
   <td class="mono">${x.uses_ok}/${x.uses_failed}</td>
   <td style="font-size:.78rem;color:var(--muted)">${x.status==="active"
     ?"approved by "+esc(x.approved_by)
     :"un-learned: "+esc(x.demoted_reason)}</td></tr>`).join("")+"</tbody></table>")
  :'<p class="empty">no rules promoted yet</p>';
 const v=await j("/api/verdicts");
 $("verdicts").innerHTML=v.length?v.map(x=>`<div class="card">
   ${chip(x.decision)} <span class="mono" style="font-size:.72rem">${esc(x.proposal_id)}</span>
   ${x.human?`<div>by <b>${esc(x.by)}</b> ${esc(x.note||"")}</div>`
   :`<div class="mono" style="font-size:.78rem">posterior ${esc(x.posterior)} vs threshold ${esc(x.threshold)}</div>
     <div style="font-size:.82rem">${esc(x.reason||"")}</div>`}
  </div>`).join(""):'<p class="empty">no verdicts yet</p>';
 const d=await j("/api/decisions");
 document.querySelector("#decisions tbody").innerHTML=d.map(x=>`<tr>
  <td class="${x.ok===false?"bad":"okmk"} mono">${x.ok==null?"-":(x.ok?"ok":"FAIL")}</td>
  <td class="mono">${esc(x.chosen)}</td><td class="mono">${esc(x.kind||"-")}</td>
  <td class="mono">${x.latency_ms==null?"-":x.latency_ms}</td>
  <td style="color:var(--muted)">${esc(x.statement)}</td></tr>`).join("");
}
async function decide(pid,action){
 const r=await j("/api/decide",{method:"POST",
  headers:{"content-type":"application/json"},
  body:JSON.stringify({proposal_id:pid,action})});
 $("msg").textContent=r.note||"";refresh();}
async function audit(){
 const t=$("q").value.trim();if(!t)return;
 const rows=await j("/api/touching?target="+encodeURIComponent(t));
 $("audit").innerHTML=rows.length?("<table><thead><tr><th>decision</th><th>role</th><th>by</th></tr></thead><tbody>"+
  rows.map(r=>`<tr><td class="mono">${esc(r.decision_id)}</td><td>${esc(r.role)}</td>
  <td>${esc(r.by||"-")}</td></tr>`).join("")+"</tbody></table>")
  :'<p class="empty">nothing has touched '+esc(t)+'</p>';}
refresh();setInterval(refresh,5000);
</script></body></html>
"""

__all__ = ["build_show_app", "show"]
