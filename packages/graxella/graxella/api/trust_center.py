"""graxella.api.trust_center — the differentiator dashboard.

One tabbed single-page UI (no CDN, no build step) that shows every
graxella differentiator over a LIVE session, recomputed from the ledger
on each refresh:

  1. observability for free   — the assertion ledger + outcome tiles
  2. fail once, learn forever — heal-ladder economics + rulebook.json
  3. governed learning        — the review queue, operable (approve /
                                reject go through Session.approve, so
                                the gate is never bypassed)
  4. cited trust              — per-tool scores, citations click through
                                to the exact ledger rows
  5. zero-token routing       — route decisions with score + decision id
  6. bounded trajectories     — typed status, hop chain, budgets spent
  7. value ledger             — tokens / cost / ok-rate per domain

Mounted by ``Session.api()`` at ``/`` and ``/ui`` — ``grx.serve()``
ships it out of the box. Everything the page shows comes from
``build_ui_data(session)``, which reads only the session's live
objects: no side-channel state, so the page agrees with the ledger
by construction.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from graxella.beliefs.records import OutcomeRecord, is_outcome_statement

_TASK_RE = re.compile(r"task=(['\"])(.*?)\1 chose=")


class UIDecision(BaseModel):
    proposal_id: str
    by: str = "operator:anonymous"
    note: str = ""


def _task_from_statement(statement: str) -> str:
    m = _TASK_RE.search(statement or "")
    return m.group(2) if m else (statement or "")[:120]


def build_ui_data(session: Any) -> dict:
    """The one JSON bundle behind the page — all of it recomputed from
    the session's ledger, gate and rulebook on every call."""
    memory, gate = session.memory, session.gate

    ledger = sorted(memory.beliefs(),
                    key=lambda r: r.get("asserted_at") or "",
                    reverse=True)[:300]

    outcome_rows = memory.beliefs(predicate="outcome")
    outcomes = [OutcomeRecord.from_statement(r["statement"])
                for r in outcome_rows if is_outcome_statement(r["statement"])]
    healed = [o for o in outcomes if o.kind == "transform"]

    routes = [{"task": _task_from_statement(r["statement"]),
               "agent": r.get("object"),
               "score": r.get("confidence"),
               "decision_id": r["id"]}
              for r in memory.beliefs(predicate="decision")
              if str(r.get("subject", "")).startswith("decision::delegate::")]
    routes.reverse()

    trajectories = []
    for r in memory.beliefs(predicate="trajectory"):
        try:
            d = json.loads(r["statement"])
        except (TypeError, ValueError):
            continue
        trajectories.append({
            "task": d.get("task", ""),
            "status": d.get("status") or r.get("object"),
            "hops": [h.get("agent") for h in d.get("hops", [])],
            "tokens_total": d.get("tokens_total"),
            "wallclock_ms": d.get("wallclock_ms")})
    trajectories.reverse()

    from graxella.gate.evidence import pending_from_ledger
    pending = pending_from_ledger(memory)
    pending_ids = {p["proposal_id"] for p in pending}

    latest_verdict: dict[str, dict] = {}
    for r in memory.beliefs(predicate="gate_verdict"):
        latest_verdict[r["subject"]] = r          # ledger order: latest wins
    decided = []
    for pid, r in latest_verdict.items():
        if pid in pending_ids:
            continue
        try:
            data = json.loads(r["statement"])
        except (TypeError, ValueError):
            data = {}
        decided.append({"proposal_id": pid,
                        "decision": r.get("object"),
                        "by": data.get("by"),
                        "note": data.get("note"),
                        "why": gate.why(pid)})
    decided.reverse()

    from graxella.healing.trust import tool_trust
    trust = {name: t.model_dump()
             for name, t in tool_trust(memory, domain=session.domain).items()}

    rb_path = session.workdir / "rulebook.json"
    return {
        "session": {"name": session.name, "domain": session.domain,
                    "model_id": session.model_id,
                    "workdir": str(session.workdir)},
        "stats": memory.outcome_stats(),
        "trust": trust,
        "pending": [{**p, "why": gate.why(p["proposal_id"])} for p in pending],
        "decided": decided,
        "rulebook": (rb_path.read_text(encoding="utf-8")
                     if rb_path.exists() else None),
        "ledger": ledger,
        "healing": {"healer_calls": session.healer_calls,
                    "drifted_served": sum(1 for o in healed if o.ok),
                    "rules_promoted": len(session.rulebook.all_rules())},
        "routes": routes,
        "trajectories": trajectories,
    }


def attach_trust_center(app: Any, session: Any) -> Any:
    """Mount the trust-center page + its data/decision endpoints onto an
    existing FastAPI app (additive — nothing already routed changes)."""
    from fastapi.responses import HTMLResponse

    @app.get("/", response_class=HTMLResponse)
    @app.get("/ui", response_class=HTMLResponse)
    def trust_center_page() -> str:
        return TRUST_CENTER_HTML

    @app.get("/ui/data")
    def trust_center_data() -> dict:
        return build_ui_data(session)

    @app.post("/ui/approve")
    def trust_center_approve(d: UIDecision) -> dict:
        result = session.approve(d.proposal_id, by=d.by, note=d.note)
        return {"ok": True, "result": type(result).__name__}

    @app.post("/ui/reject")
    def trust_center_reject(d: UIDecision) -> dict:
        session.reject(d.proposal_id, by=d.by, note=d.note)
        return {"ok": True}

    return app


TRUST_CENTER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>graxella trust center</title>
<style>
:root {
  color-scheme: light;
  --surface: #fcfcfb; --page: #f9f9f7;
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --baseline: #c3c2b7; --ring: rgba(11,11,11,0.10);
  --accent: #2a78d6; --accent-track: #cde2fb;
  --good: #0ca30c; --good-text: #006300; --critical: #d03b3b; --warning: #fab219;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --surface: #1a1a19; --page: #0d0d0d;
    --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --baseline: #383835; --ring: rgba(255,255,255,0.10);
    --accent: #3987e5; --accent-track: #10315c;
    --good-text: #0ca30c;
  }
}
* { box-sizing: border-box; margin: 0; }
body {
  background: var(--page); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  padding-bottom: 48px;
}
header {
  padding: 20px 28px 0; display: flex; flex-wrap: wrap; gap: 8px 16px;
  align-items: baseline;
}
header h1 { font-size: 19px; font-weight: 650; letter-spacing: -0.01em; }
header .sess { color: var(--ink-2); font-size: 13px; }
header .live { margin-left: auto; font-size: 12px; color: var(--ink-2); display: flex; gap: 12px; align-items: center; }
header .live a { color: var(--accent); text-decoration: none; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--good); margin-right: 5px; }
nav {
  display: flex; gap: 2px; padding: 14px 22px 0; flex-wrap: wrap;
  border-bottom: 1px solid var(--grid); position: sticky; top: 0;
  background: var(--page); z-index: 5;
}
nav button {
  appearance: none; border: 0; background: none; color: var(--ink-2);
  font: inherit; font-size: 13.5px; padding: 9px 13px 11px; cursor: pointer;
  border-bottom: 2px solid transparent; white-space: nowrap;
}
nav button:hover { color: var(--ink); }
nav button.on { color: var(--ink); font-weight: 600; border-bottom-color: var(--accent); }
nav button .n {
  font-size: 11px; background: var(--surface); border: 1px solid var(--ring);
  border-radius: 9px; padding: 0 6px; margin-left: 5px; color: var(--muted);
  font-variant-numeric: tabular-nums;
}
main { padding: 22px 28px; max-width: 1180px; }
section.tab { display: none; }
section.tab.on { display: block; }
.claim { font-size: 15px; color: var(--ink); margin-bottom: 4px; font-weight: 600; }
.sub { color: var(--ink-2); font-size: 13px; margin-bottom: 18px; max-width: 76ch; }
.tiles { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 22px; }
.tile {
  background: var(--surface); border: 1px solid var(--ring); border-radius: 10px;
  padding: 14px 18px 12px; min-width: 158px; flex: 0 1 auto;
}
.tile .v { font-size: 27px; font-weight: 650; letter-spacing: -0.02em; }
.tile .v small { font-size: 14px; font-weight: 500; color: var(--ink-2); }
.tile .k { font-size: 12px; color: var(--muted); margin-top: 2px; }
.tile.hero .v { color: var(--accent); }
.card {
  background: var(--surface); border: 1px solid var(--ring); border-radius: 10px;
  padding: 16px 18px; margin-bottom: 16px; overflow-x: auto;
}
.card h3 { font-size: 13px; font-weight: 600; color: var(--ink-2); margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.04em; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th { text-align: left; color: var(--muted); font-weight: 500; font-size: 12px; padding: 4px 14px 6px 0; border-bottom: 1px solid var(--baseline); white-space: nowrap; }
td { padding: 6px 14px 6px 0; border-bottom: 1px solid var(--grid); vertical-align: top; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; padding-right: 20px; }
.mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.id-chip {
  font-family: ui-monospace, Consolas, monospace; font-size: 11px;
  background: none; border: 1px solid var(--ring); border-radius: 6px;
  padding: 1px 6px; color: var(--accent); cursor: pointer;
}
.id-chip:hover { border-color: var(--accent); }
.status { font-size: 12px; font-weight: 600; white-space: nowrap; }
.status.ok { color: var(--good-text); }
.status.fail { color: var(--critical); }
.chip {
  display: inline-block; font-size: 12px; font-weight: 600; border-radius: 12px;
  padding: 2px 10px; border: 1px solid var(--ring);
}
.chip.completed { color: var(--good-text); }
.chip.pending { color: var(--warning); }
pre.why {
  font: 12px/1.55 ui-monospace, Consolas, monospace; color: var(--ink-2);
  background: var(--page); border: 1px solid var(--grid); border-radius: 8px;
  padding: 12px 14px; white-space: pre-wrap; margin-top: 10px;
}
.bar-row { display: grid; grid-template-columns: 170px 1fr 220px; gap: 12px; align-items: center; padding: 7px 0; }
.bar-track { height: 10px; background: var(--accent-track); border-radius: 4px; position: relative; }
.bar-fill { height: 10px; background: var(--accent); border-radius: 4px; min-width: 4px; }
.bar-val { font-weight: 650; font-variant-numeric: tabular-nums; }
.bar-meta { font-size: 12px; color: var(--ink-2); white-space: nowrap; }
.btn {
  font: inherit; font-size: 13px; font-weight: 600; border-radius: 8px;
  padding: 6px 14px; cursor: pointer; border: 1px solid var(--ring);
  background: var(--surface); color: var(--ink);
}
.btn.approve { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn:hover { filter: brightness(1.08); }
input.by {
  font: inherit; font-size: 13px; padding: 6px 10px; border-radius: 8px;
  border: 1px solid var(--baseline); background: var(--page); color: var(--ink); width: 170px;
}
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 12px; }
.filters { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.filters button {
  font: inherit; font-size: 12.5px; border: 1px solid var(--ring); background: var(--surface);
  color: var(--ink-2); border-radius: 14px; padding: 3px 12px; cursor: pointer;
}
.filters button.on { color: var(--ink); border-color: var(--accent); font-weight: 600; }
.hops { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.hop {
  border: 1px solid var(--baseline); border-radius: 8px; padding: 4px 12px;
  font-size: 13px; font-weight: 600; background: var(--surface);
}
.hop-arrow { color: var(--muted); }
.note { font-size: 12.5px; color: var(--muted); margin-top: 10px; max-width: 80ch; }
.flash { animation: flash 1.6s ease-out; }
@keyframes flash { 0% { background: var(--accent-track); } 100% { background: none; } }
.ladder td:first-child { white-space: nowrap; font-weight: 600; }
.empty { color: var(--muted); font-size: 13px; padding: 8px 0; }
.vs { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 22px; }
.vs .tile { min-width: 220px; }
.vs .tile.bad .v { color: var(--critical); }
.vs .tile.good .v { color: var(--good-text); }
</style>
</head>
<body>
<header>
  <h1>graxella trust center</h1>
  <span class="sess" id="sess"></span>
  <span class="live"><span><span class="dot"></span>live from the ledger</span>
    <a href="/docs" target="_blank">Swagger /docs</a>
    <a href="/topology" target="_blank">topology map</a>
    <button class="btn" id="refresh" style="padding:3px 12px;font-size:12px">refresh</button>
  </span>
</header>
<nav id="tabs"></nav>
<main id="main"></main>

<script>
"use strict";
const TABS = [
  ["obs",   "1 · Observability for free"],
  ["heal",  "2 · Fail once, learn forever"],
  ["gate",  "3 · Governed learning"],
  ["trust", "4 · Cited trust"],
  ["route", "5 · Zero-token routing"],
  ["traj",  "6 · Bounded trajectories"],
  ["value", "7 · Value ledger"],
];
let DATA = null, active = "obs", ledgerFilter = "all";

const $ = (s, el=document) => el.querySelector(s);
const esc = t => { const d = document.createElement("div"); d.textContent = t == null ? "" : String(t); return d.innerHTML; };
const fmt = (n, d=0) => n == null ? "–" : Number(n).toLocaleString("en-US", {maximumFractionDigits: d, minimumFractionDigits: d});
const ts = t => t ? new Date(t).toLocaleTimeString("en-GB") : "";

function tile(v, k, cls="") { return `<div class="tile ${cls}"><div class="v">${v}</div><div class="k">${esc(k)}</div></div>`; }
function idChip(id) { return `<button class="id-chip" data-goto="${esc(id)}" title="show this assertion on the ledger">${esc(String(id).slice(0,18))}…</button>`; }

function counts() {
  if (!DATA) return {};
  return { obs: DATA.ledger.length, heal: DATA.healing.drifted_served,
           gate: DATA.pending.length, trust: Object.keys(DATA.trust).length,
           route: DATA.routes.length, traj: DATA.trajectories.length, value: null };
}
function renderTabs() {
  const c = counts();
  $("#tabs").innerHTML = TABS.map(([id, label]) =>
    `<button data-tab="${id}" class="${id===active?"on":""}">${esc(label)}${c[id]!=null?`<span class="n">${c[id]}</span>`:""}</button>`).join("");
}

function rObs(d) {
  const s = d.stats.total;
  const decisions = d.ledger.filter(r => r.predicate === "decision").length;
  const rows = d.ledger.filter(r => ledgerFilter === "all" || r.predicate === ledgerFilter);
  const preds = ["all", ...new Set(d.ledger.map(r => r.predicate))];
  return `
  <div class="claim">Every tool call becomes two cited ledger assertions — a decision and an outcome. No logging code, no metrics wiring, no APM agent.</div>
  <div class="sub">Everything below is recomputed from <span class="mono">mnema.db</span> on refresh. There is no side-channel bookkeeping to drift out of sync.</div>
  <div class="tiles">
    ${tile(fmt(d.ledger.length), "assertions on the ledger", "hero")}
    ${tile(fmt(s.count), "outcomes")}
    ${tile(fmt(decisions), "decisions recorded")}
    ${tile(fmt(s.ok_rate*100,1)+`<small> %</small>`, "ok rate")}
    ${tile(s.avg_latency_ms==null?"–":fmt(s.avg_latency_ms,1)+`<small> ms</small>`, "avg latency")}
  </div>
  <div class="card"><h3>the ledger — newest first</h3>
    ${d.ledger.length ? `
    <div class="filters">${preds.map(p=>`<button data-lf="${esc(p)}" class="${p===ledgerFilter?"on":""}">${esc(p)}</button>`).join("")}</div>
    <table><thead><tr><th>time</th><th>assertion</th><th>predicate</th><th>subject</th><th>statement</th><th class="num">conf</th></tr></thead>
    <tbody>${rows.map(r=>`
      <tr id="row-${esc(r.id)}"><td>${ts(r.asserted_at)}</td><td>${idChip(r.id)}</td>
      <td>${esc(r.predicate)}</td><td class="mono">${esc(String(r.subject||"").slice(0,22))}</td>
      <td>${esc(String(r.statement).slice(0,130))}</td><td class="num">${fmt(r.confidence,2)}</td></tr>`).join("")}
    </tbody></table>` : `<div class="empty">no assertions yet — call a @grx.tool and refresh</div>`}
  </div>`;
}

function rHeal(d) {
  const h = d.healing;
  return `
  <div class="claim">A drifted API is healed by an LLM exactly once — ever. The fix ships as a reviewable artifact, not a retry loop.</div>
  <div class="sub">The heal ladder runs inside every @grx.tool(fallback=...) — cheapest rung first. The naive pattern (an LLM retry loop) pays one LLM fix per drifted call, forever.</div>
  <div class="vs">
    ${tile(fmt(h.drifted_served), "LLM fixes the naive pattern would have paid (one per drifted call)", "bad")}
    ${tile(fmt(h.healer_calls), "healer invocations in graxella — total, ever", "good")}
    ${tile(fmt(h.drifted_served), "drifted calls served successfully")}
    ${tile(fmt(h.rules_promoted), "rules promoted to the rulebook")}
  </div>
  <div class="card"><h3>the heal ladder — cheapest rung first</h3>
    <table class="ladder"><thead><tr><th>rung</th><th>what happens</th><th class="num">LLM cost</th></tr></thead><tbody>
      <tr><td>1 · happy path</td><td>primary succeeds</td><td class="num">0</td></tr>
      <tr><td>2 · promoted heal</td><td>rulebook holds a gate-approved transform → apply, call fallback</td><td class="num">0</td></tr>
      <tr><td>2.5 · proposed heal</td><td>a recipe proposed this process awaits review → reused deterministically</td><td class="num">0</td></tr>
      <tr><td>3 · heal-once</td><td>no rule yet → healer proposes a TransformRecipe ONCE; ships as a gated Proposal</td><td class="num">1 call, ever</td></tr>
      <tr><td>4 · loud failure</td><td>nothing worked → typed failure outcome, re-raise</td><td class="num">0</td></tr>
    </tbody></table></div>
  <div class="card"><h3>rulebook.json — the promoted artifact (diff-able, version-controllable)</h3>
    ${d.rulebook ? `<pre class="why">${esc(d.rulebook)}</pre>` : `<div class="empty">no rule promoted yet — approve a pending proposal in tab 3</div>`}
  </div>
  <div class="note">Whatever proposes the fix (an LLM in production), the output is a TransformRecipe — deterministic and reviewable — never a prompt.</div>`;
}

function rGate(d) {
  const pend = d.pending.map(p => `
    <div class="card" data-pid="${esc(p.proposal_id)}">
      <h3>pending · ${esc(p.kind||"proposal")} · <span class="mono">${esc(p.proposal_id)}</span></h3>
      <div><span class="chip pending">⏳ awaiting human review</span>
        &nbsp; domain <b>${esc(p.domain||"–")}</b>
        ${p.posterior!=null?` &nbsp; posterior <b>${fmt(p.posterior,2)}</b>`:""}</div>
      ${p.reason?`<div class="sub" style="margin:8px 0 0">${esc(p.reason)}</div>`:""}
      <pre class="why">${esc(p.why)}</pre>
      <div class="row">
        <input class="by" value="operator:you" title="who is deciding">
        <button class="btn approve" data-act="approve">Approve → promote</button>
        <button class="btn" data-act="reject">Reject</button>
      </div>
    </div>`).join("");
  const dec = d.decided.map(x => `
    <div class="card"><h3>${esc(x.decision)} · <span class="mono">${esc(x.proposal_id)}</span></h3>
      <div><span class="chip completed">✓ ${esc(x.decision)} by ${esc(x.by||"gate:auto")}</span>
      ${x.note?` &nbsp; <span class="sub" style="margin:0">“${esc(x.note)}”</span>`:""}</div>
      <pre class="why">${esc(x.why)}</pre></div>`).join("");
  return `
  <div class="claim">Nothing changes runtime behavior without passing the Evidence Gate — and every verdict is itself a cited ledger assertion.</div>
  <div class="sub">Cold (domain, kind) tuples always go to a human; warm tuples with strong evidence can auto-approve. Approving below records your decision on the ledger, re-decides through the gate, and only then promotes to the rulebook. This queue is read from the ledger — it survives restarts.</div>
  ${pend || `<div class="card"><div class="empty">review queue is empty — every proposal has been decided</div></div>`}
  ${dec ? `<div class="claim" style="margin-top:22px">decided — the audit trail</div>${dec}` : ""}`;
}

function rTrust(d) {
  const items = Object.values(d.trust).sort((a,b)=>b.score-a.score);
  return `
  <div class="claim">Tool reliability, computed from outcome assertions alone — and every score carries the assertion ids it was computed from.</div>
  <div class="sub">Laplace-smoothed: (successes+1)/(successes+failures+2). Click any citation to see the exact ledger row behind the number.</div>
  <div class="card"><h3>trust per tool (score, 0–1)</h3>
    ${items.length ? items.map(t=>`
      <div class="bar-row">
        <div style="font-weight:600">${esc(t.tool)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${(t.score*100).toFixed(1)}%"></div></div>
        <div class="bar-meta"><span class="bar-val">${t.score.toFixed(2)}</span>
          &nbsp; <span class="status ok">✓ ${t.successes}</span>
          &nbsp; <span class="status fail">✕ ${t.failures}</span></div>
      </div>
      <div style="margin:0 0 12px; display:flex; gap:6px; flex-wrap:wrap; align-items:center">
        <span class="note" style="margin:0">citations (${t.citations.length}):</span>
        ${t.citations.map(idChip).join(" ")}
      </div>`).join("") : `<div class="empty">no tool outcomes yet — call a @grx.tool and refresh</div>`}
  </div>`;
}

function rRoute(d) {
  return `
  <div class="claim">Agent dispatch without a runtime LLM: deterministic scoring, a cited decision id per route, zero tokens spent deciding.</div>
  <div class="sub">The supervisor-LLM pattern pays one LLM call per hop and answers “why did it route there?” with a prompt. Here every route is a replayable ledger assertion.</div>
  <div class="tiles">
    ${tile(fmt(d.routes.length), "routes decided", "hero")}
    ${tile("0", "tokens spent routing")}
  </div>
  <div class="card"><h3>route decisions — newest first</h3>
    ${d.routes.length ? `
    <table><thead><tr><th>task</th><th>→ agent</th><th class="num">score</th><th>decision id</th><th class="num">routing tokens</th></tr></thead>
    <tbody>${d.routes.map(r=>`
      <tr><td>${esc(r.task)}</td><td><b>${esc(r.agent)}</b></td>
      <td class="num">${fmt(r.score,2)}</td><td>${idChip(r.decision_id)}</td><td class="num">0</td></tr>`).join("")}
    </tbody></table>` : `<div class="empty">no routes yet — build a mesh with grx.mesh([...]) and invoke it</div>`}
  </div>`;
}

function rTraj(d) {
  return `
  <div class="claim">Multi-hop runs are bounded by construction: hard budgets, loop detection, and a typed result — runaway agent ping-pong is impossible, not just unlikely.</div>
  <div class="sub">Every trajectory ends in a typed status: completed · loop_detected · budget_exhausted · failed. The chain is a ledger object derived from every hop decision.</div>
  ${d.trajectories.length ? d.trajectories.map(t=>`
    <div class="card"><h3>trajectory · ${esc(t.task)}</h3>
      <div class="row" style="margin-top:0">
        <span class="chip ${t.status==="completed"?"completed":"pending"}">${t.status==="completed"?"✓":"⚠"} ${esc(t.status)}</span>
        <span class="sub" style="margin:0">tokens ${fmt(t.tokens_total)} · wallclock ${fmt(t.wallclock_ms)} ms</span>
      </div>
      <div class="hops" style="margin-top:12px">
        <span class="hop" style="border-style:dashed">task</span>
        ${t.hops.map(h=>`<span class="hop-arrow">→</span><span class="hop">${esc(h)}</span>`).join("")}
      </div>
    </div>`).join("") : `<div class="card"><div class="empty">no trajectories yet — run app.run_trajectory(task, max_hops=...)</div></div>`}`;
}

function rValue(d) {
  const s = d.stats.total, dom = d.stats.by_domain||{};
  return `
  <div class="claim">The value ledger: count, ok-rate, tokens, cost and latency — recomputed from ledger assertions alone, per domain.</div>
  <div class="sub">Nothing is estimated; everything is counted. Token and cost figures stay 0 until a real LLM runs on the substrate — then these same tiles fill in from the same assertions.</div>
  <div class="tiles">
    ${tile(fmt(s.count), "outcomes", "hero")}
    ${tile(fmt(s.ok_rate*100,1)+`<small> %</small>`, "ok rate")}
    ${tile(fmt(s.tokens_in)+`<small> in</small> · `+fmt(s.tokens_out)+`<small> out</small>`, "tokens")}
    ${tile(`$`+fmt(s.cost_usd,4), "cost")}
    ${tile(fmt(s.violations), "constitution violations")}
  </div>
  <div class="card"><h3>by domain</h3>
    <table><thead><tr><th>domain</th><th class="num">count</th><th class="num">ok</th><th class="num">ok rate</th><th class="num">tokens in</th><th class="num">tokens out</th><th class="num">cost $</th><th class="num">violations</th></tr></thead>
    <tbody>${Object.entries(dom).map(([k,v])=>`
      <tr><td><b>${esc(k)}</b></td><td class="num">${fmt(v.count)}</td><td class="num">${fmt(v.ok)}</td>
      <td class="num">${fmt(v.ok_rate*100,1)} %</td><td class="num">${fmt(v.tokens_in)}</td>
      <td class="num">${fmt(v.tokens_out)}</td><td class="num">${fmt(v.cost_usd,4)}</td>
      <td class="num">${fmt(v.violations)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

const RENDER = { obs: rObs, heal: rHeal, gate: rGate, trust: rTrust, route: rRoute, traj: rTraj, value: rValue };

function render() {
  if (!DATA) return;
  renderTabs();
  const s = DATA.session;
  $("#sess").textContent = `session ${s.name}` +
    (s.domain ? ` · domain ${s.domain}` : "") +
    (s.model_id ? ` · model ${s.model_id}` : "");
  const el = document.createElement("section");
  el.className = "tab on";
  el.innerHTML = RENDER[active](DATA);
  $("#main").replaceChildren(el);
}

async function load() {
  const r = await fetch("/ui/data");
  DATA = await r.json();
  render();
}

document.addEventListener("click", async e => {
  const t = e.target.closest("[data-tab],[data-lf],[data-goto],[data-act]");
  if (!t) return;
  if (t.dataset.tab) { active = t.dataset.tab; render(); }
  else if (t.dataset.lf) { ledgerFilter = t.dataset.lf; render(); }
  else if (t.dataset.goto) {
    active = "obs"; ledgerFilter = "all"; render();
    const row = document.getElementById("row-" + t.dataset.goto);
    if (row) { row.scrollIntoView({behavior:"smooth", block:"center"}); row.classList.add("flash"); }
  }
  else if (t.dataset.act) {
    const card = t.closest("[data-pid]");
    const body = { proposal_id: card.dataset.pid, by: $(".by", card).value || "operator:anonymous",
                   note: t.dataset.act === "approve" ? "approved from trust center" : "rejected from trust center" };
    t.disabled = true;
    await fetch("/ui/" + t.dataset.act, { method: "POST",
      headers: {"content-type": "application/json"}, body: JSON.stringify(body) });
    await load();
  }
});
$("#refresh").addEventListener("click", load);
load();
</script>
</body>
</html>
"""

__all__ = ["TRUST_CENTER_HTML", "attach_trust_center", "build_ui_data"]
