"""graxella.api.topology — the agent-topology map (task 3-6, Beat 2).

The view generic APM can't give: the mesh as a graph — agents with
their skills, live cited tool-trust, route traffic per agent, and the
governance hot spots (escalations, mismatches). Two surfaces:

  * ``topology_data(app)``  — the JSON an operator UI (Cytoscape, D3,
    anything) renders; also served by the control plane if mounted.
  * ``render_html(data)``   — a dependency-free single-file HTML canvas
    renderer for quick local inspection (no CDN, no build step).
"""
from __future__ import annotations

import json
import math
from typing import Any


def topology_data(app: Any) -> dict:
    """Nodes + edges from the ledger and the mesh — every number cited
    or derivable from assertions."""
    from axon_fabric.trust import tool_trust

    domain = app.domain or app.memory.namespace
    routes: dict[str, int] = {}
    ok_counts: dict[str, int] = {}
    for row in app.memory.beliefs(predicate="outcome"):
        from graxella.beliefs.records import OutcomeRecord, is_outcome_statement
        if not is_outcome_statement(row["statement"]):
            continue
        rec = OutcomeRecord.from_statement(row["statement"])
        agent = (rec.chosen or "?").split("::")[0]
        routes[agent] = routes.get(agent, 0) + 1
        if rec.ok:
            ok_counts[agent] = ok_counts.get(agent, 0) + 1

    escalations: dict[str, int] = {}
    for sig in app.memory.signals():
        agent = str(sig.get("agent") or "?").split("::")[0]
        escalations[agent] = escalations.get(agent, 0) + 1

    nodes = []
    for desc in app.society.describe():
        name = desc.get("name") or desc.get("agent") or "?"
        n = routes.get(name, 0)
        nodes.append({
            "id": name,
            "kind": "agent",
            "skills": [s.get("name", "") for s in (desc.get("skills") or [])][:5],
            "routes": n,
            "ok_rate": round(ok_counts.get(name, 0) / n, 3) if n else None,
            "signals": escalations.get(name, 0),
        })
    for tool, t in tool_trust(app.memory, domain=domain).items():
        nodes.append({"id": tool, "kind": "tool", "trust": t.score,
                      "n": t.n, "citations": len(t.citations)})

    edges = [{"source": "mesh", "target": n["id"], "weight": n.get("routes", 0)}
             for n in nodes if n["kind"] == "agent"]
    return {"domain": domain, "nodes": nodes, "edges": edges}


def render_html(data: dict) -> str:
    """Self-contained HTML: agents on a ring sized by traffic, colored by
    ok-rate; tools listed with cited trust. Zero dependencies."""
    payload = json.dumps(data)
    return f"""<!doctype html><meta charset="utf-8">
<title>graxella topology — {data.get('domain', '')}</title>
<style>body{{font-family:Consolas,monospace;background:#121815;color:#E2E9E4;margin:20px}}
canvas{{background:#1A211D;border:1px solid #2A342E;border-radius:6px}}
.legend{{font-size:12px;color:#94A29A;margin-top:8px}}</style>
<h3>graxella mesh — domain: {data.get('domain', '')}</h3>
<canvas id="c" width="900" height="560"></canvas>
<div class="legend">node size = route traffic · green = healthy ok-rate ·
amber = degrading · red ring = governance signals · tools listed with cited trust</div>
<script>
const d = {payload};
const ctx = document.getElementById('c').getContext('2d');
const agents = d.nodes.filter(n => n.kind === 'agent');
const tools = d.nodes.filter(n => n.kind === 'tool');
const cx = 450, cy = 250, R = 180;
ctx.font = '11px Consolas';
agents.forEach((n, i) => {{
  const a = 2 * Math.PI * i / Math.max(agents.length, 1) - Math.PI / 2;
  const x = cx + R * Math.cos(a), y = cy + R * Math.sin(a);
  ctx.strokeStyle = '#2A342E';
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke();
  const r = 8 + Math.min(22, 3 * Math.sqrt(n.routes || 0));
  const ok = n.ok_rate;
  ctx.fillStyle = ok == null ? '#5C6A63' : ok > 0.9 ? '#43C495'
                 : ok > 0.6 ? '#D9A05B' : '#D97B63';
  ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
  if (n.signals > 0) {{
    ctx.strokeStyle = '#D97B63'; ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(x, y, r + 4, 0, 7); ctx.stroke();
    ctx.lineWidth = 1;
  }}
  ctx.fillStyle = '#E2E9E4';
  ctx.fillText(n.id + (n.routes ? ' (' + n.routes + ')' : ''), x + r + 4, y + 3);
}});
ctx.fillStyle = '#43C495';
ctx.beginPath(); ctx.arc(cx, cy, 6, 0, 7); ctx.fill();
ctx.fillStyle = '#94A29A'; ctx.fillText('router', cx + 10, cy + 3);
tools.forEach((t, i) => {{
  ctx.fillStyle = '#94A29A';
  ctx.fillText('tool ' + t.id + '  trust=' + t.trust + '  (n=' + t.n +
               ', ' + t.citations + ' citations)', 20, 500 + 16 * i);
}});
</script>"""


__all__ = ["topology_data", "render_html"]
