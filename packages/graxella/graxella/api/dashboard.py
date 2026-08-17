"""graxella.api.dashboard — single-file zero-build operator dashboard.

Layout after Beat 2 step 1:
  * full-width **agent topology** mindmap (Cytoscape.js via CDN)
  * two-column bottom: pending proposals + constitution violations
The raw tracer-events table is intentionally gone; step 2 wires the tracer
to OpenTelemetry so trace exploration happens in Jaeger/Tempo, not here.
"""
from __future__ import annotations

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>graxella runtime</title>
<script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
<style>
  :root {
    --bg:#f8fafc; --panel:#ffffff; --edge:#e2e8f0; --ink:#0f172a;
    --muted:#64748b; --accent:#2563eb; --accent-soft:#dbeafe;
    --ok:#16a34a; --warn:#d97706; --err:#dc2626; --pending:#ca8a04;
    --header:#1e40af; --shadow:0 1px 3px rgba(15,23,42,0.06);
  }
  * { box-sizing: border-box; }
  body { margin:0; font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background:var(--bg); color:var(--ink); }
  header { padding:14px 22px; background:var(--header); color:#fff; border-bottom:1px solid var(--edge);
           display:flex; justify-content:space-between; align-items:center; }
  header h1 { margin:0; font-size:16px; font-weight:600; letter-spacing:0.02em; color:#fff; }
  header .health { font-size:12px; color:#dbeafe; }
  header .health .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
  main { display:grid; grid-template-columns: repeat(2, 1fr); gap:16px; padding:16px; }
  .panel { background:var(--panel); border:1px solid var(--edge); border-radius:8px;
           padding:14px 16px; min-height:360px; box-shadow:var(--shadow); }
  .panel-full { grid-column: 1 / -1; min-height:460px; }
  .panel h2 { font-size:12px; margin:0 0 12px; text-transform:uppercase; letter-spacing:0.08em;
              color:var(--accent); font-weight:700; }
  .panel h2 .count { color:var(--muted); font-weight:normal; margin-left:6px; }
  .panel h2 .legend { float:right; color:var(--muted); font-weight:normal; font-size:11px;
                      text-transform:none; letter-spacing:0; }
  .panel h2 .legend .sq { display:inline-block; width:10px; height:10px; margin:0 4px 0 12px;
                          vertical-align:middle; border-radius:2px; }
  #topology-cy { width:100%; height:400px; background:#f1f5f9; border-radius:6px;
                 border:1px solid var(--edge); }
  .row { border-top:1px solid var(--edge); padding:9px 0; font-size:12px; }
  .row:first-of-type { border-top:none; padding-top:2px; }
  .row .head { display:flex; justify-content:space-between; margin-bottom:4px; align-items:center; }
  .row .id { color:var(--muted); font-family: ui-monospace, Menlo, Consolas, monospace; font-size:11px; }
  .row .kind { font-weight:600; color:var(--ink); }
  .row .meta { color:var(--muted); font-size:11px; margin-top:3px;
               font-family: ui-monospace, Menlo, Consolas, monospace; word-break:break-all; }
  .badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px;
           text-transform:uppercase; letter-spacing:0.05em; font-weight:600; }
  .b-narrow { background:var(--accent-soft); color:#1e40af; }
  .b-wide { background:#fef3c7; color:#92400e; }
  .b-unknown { background:#f1f5f9; color:#475569; }
  .s-pending { color:var(--pending); }
  .s-approved { color:var(--accent); }
  .s-active { color:var(--ok); }
  .s-rejected { color:var(--err); }
  .s-error { color:var(--err); font-weight:600; }
  .s-warning { color:var(--warn); font-weight:600; }
  .actions { margin-top:8px; display:flex; gap:6px; }
  button { background:#fff; color:var(--accent); border:1px solid var(--accent);
           padding:4px 12px; border-radius:5px; font-size:11px; font-weight:600; cursor:pointer;
           transition: background 0.12s, color 0.12s; }
  button:hover { background:var(--accent); color:#fff; }
  button.reject { color:var(--err); border-color:var(--err); }
  button.reject:hover { background:var(--err); color:#fff; }
  button.eval { color:var(--pending); border-color:var(--pending); }
  button.eval:hover { background:var(--pending); color:#fff; }
  .empty { color:var(--muted); font-style:italic; padding:20px 0; text-align:center; }
  footer { padding:10px 22px; color:var(--muted); font-size:11px;
           border-top:1px solid var(--edge); background:var(--panel); }
  .btn-refresh { float:right; }
</style>
</head>
<body>

<header>
  <h1>graxella runtime</h1>
  <span class="health" id="health">
    <span class="dot" style="background:#475569"></span>connecting...
  </span>
</header>

<main>
  <section class="panel panel-full">
    <h2>Agent topology
        <span class="count" id="topology-count">(0 nodes)</span>
        <span class="legend">
          <span class="sq" style="background:#2563eb"></span>agent
          <span class="sq" style="background:#ffffff;border:1.5px solid #93c5fd"></span>tool / skill
          <span class="sq" style="background:#2563eb;height:2px;margin-top:6px"></span>invoked edge (thicker = more calls)
        </span>
    </h2>
    <div id="topology-cy"></div>
  </section>

  <section class="panel">
    <h2>Pending proposals <span class="count" id="pending-count">(0)</span>
        <button class="btn-refresh" onclick="refresh()">refresh</button></h2>
    <div id="pending"></div>
  </section>

  <section class="panel">
    <h2>Constitution violations <span class="count" id="viol-count">(0)</span></h2>
    <div id="violations"></div>
  </section>
</main>

<footer>
  Auto-refresh every 5s &middot; Detection-only governance: nothing here silently mutates the runtime.
  &middot; Trace waterfalls will live in Jaeger once the OTel adapter lands (Beat 2 step 2).
</footer>

<script>
const API = '';  // same origin
let cy = null;
let lastNodeIds = '';

async function jget(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(path + ' -> ' + r.status);
  return r.json();
}

async function jpost(path, body) {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(path + ' -> ' + r.status);
  return r.json();
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>\"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
  ));
}

function renderHealth(h) {
  const el = document.getElementById('health');
  if (!h || !h.ok) {
    el.innerHTML = '<span class="dot" style="background:var(--err)"></span>down';
    return;
  }
  const parts = Object.entries(h.components || {})
    .filter(([, v]) => v)
    .map(([k]) => k);
  el.innerHTML = '<span class="dot" style="background:var(--ok)"></span>'
    + 'components: ' + escapeHtml(parts.join(', '));
}

function renderTopology(g) {
  const nodes = g.nodes || [];
  const edges = g.edges || [];
  document.getElementById('topology-count').textContent =
    '(' + nodes.length + ' nodes, ' + edges.length + ' edges)';

  const nodeIdsKey = nodes.map(n => n.id).sort().join('|');
  const structureChanged = nodeIdsKey !== lastNodeIds;
  lastNodeIds = nodeIdsKey;

  const elements = [
    ...nodes.map(n => ({data: {id: n.id, label: n.label, type: n.type}})),
    ...edges.map(e => ({data: {
      id: e.source + '->' + e.target,
      source: e.source, target: e.target,
      kind: e.kind, calls: e.calls || 0,
    }})),
  ];

  if (!cy) {
    cy = cytoscape({
      container: document.getElementById('topology-cy'),
      elements,
      style: [
        { selector: 'node[type = "agent"]', style: {
            'background-color': '#2563eb', 'label': 'data(label)',
            'color': '#ffffff', 'text-valign': 'center', 'text-halign': 'center',
            'font-size': 12, 'font-weight': 700,
            'width': 56, 'height': 56,
            'border-width': 3, 'border-color': '#1e40af',
        }},
        { selector: 'node[type = "skill"]', style: {
            'background-color': '#ffffff', 'label': 'data(label)',
            'color': '#1e40af', 'text-valign': 'center', 'text-halign': 'center',
            'font-size': 10, 'font-weight': 600,
            'width': 'label', 'height': 24, 'padding': '8px',
            'shape': 'round-rectangle',
            'border-width': 1.5, 'border-color': '#93c5fd',
        }},
        { selector: 'edge', style: {
            'width': 'mapData(calls, 0, 5, 1.5, 6)',
            'line-color': '#cbd5e1',
            'target-arrow-color': '#cbd5e1',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'arrow-scale': 1.0,
        }},
        { selector: 'edge[calls > 0]', style: {
            'line-color': '#2563eb',
            'target-arrow-color': '#2563eb',
            'label': 'data(calls)',
            'font-size': 10, 'font-weight': 700, 'color': '#1e40af',
            'text-background-color': '#ffffff',
            'text-background-opacity': 1,
            'text-background-padding': 3,
            'text-border-width': 1,
            'text-border-color': '#93c5fd',
            'text-border-opacity': 1,
            'text-margin-y': -6,
        }},
      ],
      layout: {name:'cose', animate:false, padding:24, nodeRepulsion:8000, idealEdgeLength:70},
    });
    return;
  }

  if (structureChanged) {
    cy.elements().remove();
    cy.add(elements);
    cy.layout({name:'cose', animate:false, padding:24, nodeRepulsion:8000, idealEdgeLength:70}).run();
  } else {
    edges.forEach(e => {
      const el = cy.getElementById(e.source + '->' + e.target);
      if (el && el.length) el.data('calls', e.calls || 0);
    });
  }
}

function renderPending(rows) {
  const host = document.getElementById('pending');
  document.getElementById('pending-count').textContent = '(' + rows.length + ')';
  if (!rows.length) { host.innerHTML = '<div class="empty">no pending proposals</div>'; return; }
  host.innerHTML = rows.map(p => {
    const obj = p.objectives || {};
    const meta = 'scalar=' + (p.score ?? 0).toFixed(3)
      + (obj.compliance !== undefined ? ' compliance=' + obj.compliance.toFixed(2) : '')
      + (obj.quality !== undefined ? ' quality=' + obj.quality.toFixed(2) : '')
      + (obj.cost_usd !== undefined ? ' cost=$' + obj.cost_usd.toFixed(3) : '')
      + (obj.latency_ms !== undefined ? ' lat=' + obj.latency_ms + 'ms' : '');
    return `
      <div class="row" data-id="${p.id}">
        <div class="head">
          <span><span class="id">#${p.id}</span> <span class="kind">${escapeHtml(p.kind)}</span></span>
          <span class="badge b-${escapeHtml(p.blast_radius)}">${escapeHtml(p.blast_radius)}</span>
        </div>
        <div class="meta">${escapeHtml(meta)}</div>
        <div class="meta">${escapeHtml(JSON.stringify(p.payload))}</div>
        <div class="actions">
          <button onclick="doAction(${p.id}, 'approve')">approve</button>
          <button class="reject" onclick="doAction(${p.id}, 'reject')">reject</button>
          <button class="eval" onclick="doAction(${p.id}, 'auto_evaluate')">auto-evaluate</button>
        </div>
      </div>`;
  }).join('');
}

function renderViolations(vs) {
  const host = document.getElementById('violations');
  document.getElementById('viol-count').textContent = '(' + vs.length + ')';
  if (!vs.length) { host.innerHTML = '<div class="empty">no violations</div>'; return; }
  const rev = vs.slice().reverse();
  host.innerHTML = rev.map(v => `
    <div class="row">
      <div class="head">
        <span class="s-${escapeHtml(v.severity)}">${escapeHtml(v.severity)}</span>
        <span class="id">seq=${v.seq}</span>
      </div>
      <div class="head">
        <span class="kind">${escapeHtml(v.name || 'unnamed')}</span>
        <span class="id">${escapeHtml(v.applies_to || '')}</span>
      </div>
      <div class="meta">${escapeHtml(v.detail || '')}</div>
      <div class="meta">decision: ${escapeHtml(v.decision_id || '-')}</div>
    </div>`).join('');
}

async function doAction(id, action) {
  const by = prompt('Operator id for ' + action + ' on proposal #' + id + ':', 'operator');
  if (!by) return;
  let note = '';
  if (action === 'approve' || action === 'reject') {
    note = prompt('Note (optional):', '') || '';
  }
  try {
    await jpost('/gate/proposals/' + id + '/' + action, {by, note});
    await refresh();
  } catch (e) {
    alert('failed: ' + e.message);
  }
}

async function refresh() {
  try {
    const [h, topo, pending, viols] = await Promise.all([
      jget('/healthz'),
      jget('/topology/graph'),
      jget('/gate/pending'),
      jget('/constitution/violations?limit=25'),
    ]);
    renderHealth(h);
    renderTopology(topo);
    renderPending(pending);
    renderViolations(viols);
  } catch (e) {
    console.error(e);
    document.getElementById('health').innerHTML =
      '<span class="dot" style="background:var(--err)"></span>error: ' + escapeHtml(e.message);
  }
}

refresh();
setInterval(refresh, 5000);
</script>

</body>
</html>
"""
