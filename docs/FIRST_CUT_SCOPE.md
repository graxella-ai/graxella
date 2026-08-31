# Graxella — First Cut Scope

## The problem we attack

**Agent behavior changes constantly in production, and there is no
accountable way to govern that change.**

Traditional software governs behavior change through a whole discipline —
version control, review, CI gates, deploys, audit logs. Every change is
proposed, approved, recorded, and reversible; someone owns it, someone can
explain it, and it can be rolled back.

Agents have none of that, yet their behavior mutates continuously — from the
outside (a tool contract an agent depends on changes underneath it), from the
model (non-determinism: the same input yields different actions), and from
memory (accumulated state changes what the agent does over time). Nobody
approved the change, nobody can explain it, nobody can reconstruct or reverse
it. **That missing change process — not model quality — is why ~88% of
enterprise agent pilots never reach production, and why the industry's worst
agent incident took five days just to detect.**

Graxella is the accountable change-control layer for agent behavior: the
`git + review + CI + audit log` that agents never had.

> **Framing discipline.** We name the *problem*, never a symptom. A tool
> contract changing under an agent is the **first instance we make
> provable** — the wedge — because it is the most concrete and demonstrable.
> It is the door in, not the ceiling. We do not claim this first cut governs
> every form of behavior change; we claim it proves the thesis on the
> instance that bites first.

---

## 1. One sentence

> Graxella makes changes to an agent's behavior **accountable** — proposed
> with cited evidence, gated by a human, applied deterministically, and fully
> auditable — starting with the changes that come from the tools an agent
> depends on shifting underneath it.

## 2. The wedge — the first place we prove it

When a tool contract an agent relies on changes, the agent's behavior changes
with it — silently. Today that either breaks the agent or burns an LLM retry
on every call, with no record of what changed or who decided the response.
Graxella closes that into an accountable loop:

> **detect the change → propose a response, cited → human approves →
> apply deterministically (zero LLM) → full audit trail.**

Demonstrated end-to-end on **one** real pipeline, with numbers. This is the
provable instance of the central problem — not the whole of it.

## 3. Decision (2026-08-29)

Ship a **design-partner pilot**, for a **platform engineer who authors
pipelines in code**, in a **~2–3 week tight wedge**. The goal is not
features — it is to get one real pipeline's **evidence ledger accumulating**
in front of one real user, because that ledger (the accountable history of
behavior change) is the moat, and every day it runs is defensibility a
competitor cannot retroactively build.

## 4. Primary user & interaction model

- **Author:** platform engineer, in a notebook / Python
  (`graxella.mesh([...])` + `langchain.agents.create_agent`). Already works.
- **Observe & govern:** a UI that is a **view** over the pipeline — a live
  topology map and an approval inbox. Not an authoring canvas.

---

## 5. Scope — what ships

**[EXISTS]** = already in the repo, reviewed & tested. **[BUILD]** = the
actual 2–3 weeks of work.

| # | Deliverable | Status |
|---|---|---|
| 1 | **Dense embeddings** in routing (Ollama `nomic-embed-text`) via the existing `embed_fn` contract | contract pluggable **[EXISTS]**; dense adapter + wiring **[BUILD]** |
| 2 | **Routing quality benchmark** — dense vs the legacy lexical baseline (a number, not a vibe) | harness pattern **[EXISTS]**; new comparison **[BUILD]** |
| 3 | **The accountable change loop** wired on ONE reference pipeline (refund desk, notebook 08): detect a tool-contract change → cited proposal → gate → deterministic response | change-detection + Evidence Gate, benchmarked **98%↓ LLM / 47× latency [EXISTS]**; wire to pipeline **[BUILD]** |
| 4 | **Evidence ledger** — the accountable history of every behavior change, persisted (the flywheel + the moat) | Mnema buffered SQLite **[EXISTS]** |
| 5 | **Governance UI** — two views: (a) live topology map, (b) approval inbox (approve/reject round-trips to the live pipeline) | `topology_data` + `render_html` + `control_plane` REST (`/gate/pending`, `/gate/why`, `/gate/approve`, `/gate/reject`) **[EXISTS]**; the app **[BUILD]** |
| 6 | **Demo + numbers panel**: a behavior change caught live → node goes red → cited proposal → human approves → deterministic response → `gate.why` audit → before/after metrics | benchmark numbers **[EXISTS]**; demo wiring **[BUILD]** |

**Reused, no work:** Evidence Gate (the review gate), the change-response
ladder, Mnema ledger (version control + audit), PROV-O export, LangChain
`create_agent` adapter, topology + control-plane APIs, the benchmark. The
first cut is mostly **wiring what exists into one legible, accountable loop.**

## 6. Explicitly OUT (roadmap, not first cut)

Each was considered and deferred — naming them is the discipline:

- Governing behavior change from **model non-determinism** and **memory
  accumulation** (the rest of the central problem — proven *after* the wedge).
- DSPy **entity-extraction** module (design-approved; build after pilot pull).
- DSPy **privacy / egress gate** (highest-value *next* thing; still next).
- **RL / GRPO** anything — rejected (GPU + contradicts the zero-LLM thesis).
- Multi-hop runtime.
- **Visual pipeline authoring / canvas** (the n8n path).
- **sentence-transformers / torch** — using Ollama dense instead (no 2 GB
  install, no Windows long-path issue).
- Guardrails.ai integration.
- pip packaging, docs site, multi-tenant, auth, RBAC.

Rule: graxella runs and delivers the wedge with **zero** optional adapters
installed. Every future integration is additive.

---

## 7. Build plan (3 weeks)

**Week 1 — the accountable loop is real end-to-end**
- Dense `embed_fn` (Ollama `nomic-embed-text`); legacy lexical fallback intact.
- Routing benchmark: dense vs lexical → commit the number to `RESULTS.md`.
- Wire the change loop to the refund-desk reference pipeline; confirm the
  ledger persists every decision + outcome across a run.
- *(Parallel, non-code:)* start design-partner outreach — the real bottleneck.

**Week 2 — the governance UI**
- One app (Streamlit first, the Mnema-demo pattern), two tabs: **Topology**
  (`topology_data` → Cytoscape) and **Approval inbox** (`/gate/pending` → row
  → `/gate/why` → approve/reject).
- Approve/reject round-trips: a human decision in the UI changes what the live
  pipeline does on the next call. **This is the hero interaction** — it is
  change-control made visible.

**Week 3 — demo, harden, dry-run**
- Demo script + numbers panel (98%↓ LLM, 47× latency, dense-vs-lexical lift).
- Harden exactly two paths: the happy path and the change→govern→respond path.
- Dry-run with a friendly user; keep a buffer day.

## 8. Definition of done (the demo that proves it)

A design partner watches, on **their** kind of pipeline:

1. A code-authored refund-desk agent runs normally; topology shows healthy.
2. A tool contract it depends on changes mid-session.
3. The topology node goes **red**; a **cited proposal** for the response
   appears in the approval inbox.
4. The human clicks **approve** — the accountable decision, on the record.
5. Subsequent calls apply the approved response **deterministically — zero
   LLM**.
6. `gate.why` / `why_believed` shows the **full audit trail** of the change
   and who governed it.
7. Numbers panel: **98% fewer LLM calls, 47× lower latency**, dense-vs-lexical
   routing lift.
8. The **ledger has accumulated** the cited history of the change — the moat
   spun up.

If a viewer sees that and says *"can I run my pipeline through this"* — the
problem is real to them and the wedge has pull.

## 9. Kill test

Put the pilot in front of **1–3 platform engineers running agents in
production.** Success signal is **"can I try it on my stack,"** not
"interesting." If three real conversations produce zero pull, **stop** — a
validated no, cheaply bought, is worth more than another month of building.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Sounding like we solve a symptom | Problem-first language everywhere; the tool-contract case is named as *the first provable instance*, never the identity |
| Overclaiming we govern *all* behavior change | Section 6 states plainly what the first cut does not yet cover |
| Ollama dependency for dense embeddings / responses | Legacy lexical fallback stays (additive); document the `ollama pull` |
| UI scope creep into a canvas/authoring tool | Two views only — read + approve. Enforced by this doc. |
| Design-partner recruiting is the true bottleneck | Start outreach Week 1, in parallel with code |
| Reference pipeline too toy to convince | Refund desk (notebook 08) — a recognizable enterprise task |
