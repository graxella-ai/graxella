# agent2society

> The transparent coordination layer for A2A agent meshes.
>
> Sits on top of [A2A](https://a2aproject.github.io/A2A/). Replaces opaque, LLM-negotiated coordination with a deterministic, graph-routed dispatch that produces a **human-readable explanation** for every decision — which agent was chosen, the features that drove the score, the alternatives that were rejected and why, the chosen agent's self-declared limits, and the upstream decision chain that led here.

```python
from agent2society import Society, Handoff

society = Society()
society.add("https://research-agent.acme.com/.well-known/agent-card.json")
society.add("https://writer-agent.acme.com/.well-known/agent-card.json")

h = Handoff(task="Summarise Q3 churn and draft an exec memo")
result_text = society.run(h)
print(society.explain(h.id).render())
```

## Features

- **Agent capabilities** — capability graph parsed from A2A Agent Cards; one cheap embedding lookup per task instead of a supervisor LLM.
- **Conflict resolution** — deterministic ranking with conformance guardrails (declared-skill + boundary allow/deny), runner-up rejection reasons recorded on every decision.
- **Governance** — detection-only hooks for low confidence, conflicts, capability drift, and human review. agent2society surfaces signals; it never silently auto-corrects.
- **Explainability** — a human-readable `RoutingExplanation` for every dispatch, keyed by handoff id. No LLM rendered it; it's a template over the features the router actually used.
- **Routing-quality signals** (v0.5.2) — every explanation carries a `margin` (top-1 vs top-2 score gap) and structured `flags`: `OOD`, `VECTOR_AMBIGUITY`, `LOW_MARGIN`. Surface them on a dashboard, gate on them, or register an `on_low_margin` hook.
- **Production ops** (v0.5) — thread-safe Society, Prometheus-format metrics, pluggable persistent ledger, auto-retry / fallback dispatch, optional LLM-assisted optimizer (LLM proposes, backtest decides).
- **Observability** (v0.5.3) — `SessionTracer` joins the telemetry sink and the explanation store into an ordered sequence of `TraceEvent`s per session. Zero new per-route cost — built from already-recorded state.
- **Production hardening** (v0.5.3) — copy-on-write boundary edits, bounded governance memory, NFKC-normalised conformance (closes unicode bypass), total `extract_text`, surfaced hook exceptions, JSONL corruption tracking. 112 tests, 16 dedicated to failure-mode regressions.

Multi-agent systems are powerful and untrustworthy. The standard supervisor pattern hides every routing choice behind an LLM that "decided"; debugging a wrong handoff means reading a transcript and guessing. `agent2society` is the opposite: every decision is a deterministic graph traversal you can read, a confidence score you can threshold on, and an audit trail keyed by handoff id.

The wedge is one cheap embedding lookup instead of a supervisor LLM — that's how the cost story works. The reason developers keep it is the explanations, the conformance guardrail, and the governance hooks.

## What agent2society does

| Layer | What it gives you |
|---|---|
| **Routing + dispatch** | Capability graph parsed from A2A Agent Cards, deterministic TF-IDF (or pluggable embedding) router, graph-derived conformance guardrail, A2A JSON-RPC dispatcher. |
| **Meaning + context** (v1.5) | `Handoff` envelope that carries intent, assumptions, and an upstream decision chain alongside the task. `SelfAssessment` — an agent's declared limits, confidence model, and escalation triggers. |
| **Transparency + governance** (v1.5) | `RoutingExplanation` for every decision (success *or* failure). Detection-only hooks: `on_conflict`, `on_low_confidence`, `on_capability_drift`, `on_human_review`. **agent2society never silently auto-corrects** — it surfaces signals so humans (or handlers you wire up) decide. |

## Install

```bash
pip install agent2society
pip install "agent2society[http]"          # httpx for HTTP dispatch
pip install "agent2society[embeddings]"    # sentence-transformers for stronger routing
```

## Live demo on Google A2A — see the layer in action

[`demos/a2a_realtime/`](demos/a2a_realtime/) runs a real customer-support pipeline on **Google's official `a2a-sdk` 1.1.0** — four FastAPI/JSON-RPC agent servers, byte-identical across both runs — then routes 20 tickets through them twice: once with a hand-rolled supervisor, once with `Society()` on top. Measured on the same agents, same tickets, same machine:

| Metric (20 tickets)            | A2A only        | A2A + agent2society       |
| ------------------------------ | --------------- | ------------------------- |
| Routing accuracy               | 11/20 (55%)     | **12/20 (60%)**           |
| Coordination tokens (LLM sup.) | 7,613           | **0** (TF-IDF, no LLM)    |
| Cost / 1k tickets @ Opus 4     | $18.73          | **$9.23**                 |
| PII tickets blocked            | 0               | **2** (SSN + DOB)         |
| Governance alerts surfaced     | 0               | **17**                    |
| A2A agent files modified       | 0 (baseline)    | **0 — purely additive**   |

```bash
cd demos/a2a_realtime
python compare.py                      # CLI run, prints the comparison table
jupyter notebook a2a_vs_agent2society.ipynb   # full walkthrough
```


## 60-second tour

```python
from agent2society import Society, Handoff

society = Society()
society.add("https://research-agent.acme.com/.well-known/agent-card.json")
society.add("https://writer-agent.acme.com/.well-known/agent-card.json")
society.add(my_crewai_crew)                                  # adapters wrap native agents

society.boundary("writer-agent", deny=["financial-data"])    # tighten beyond the card

# Strings still work.
result = society.run("Summarise Q3 churn and draft an exec memo")

# A Handoff carries why the task exists, what we assume, and prior decisions.
h = Handoff(
    task="Draft an executive memo on Q3 customer churn",
    intent="prep the board pack",
    assumptions=["churn data is final"],
    confidence_required=0.5,
)
memo = society.run(h)

# Every decision has a human-readable explanation, keyed by handoff id.
print(society.explain(h.id).render())

society.report()      # per-task ledger: tokens, routing, conformance, explanations
```

> `Mesh` remains as an alias for `Society` — code written against the v0 surface keeps working.

## What an explanation looks like

```text
handoff a77d6eed5a15  task: Draft an executive memo on customer churn
  intent: prep the Q3 board pack
  assumes:
    - churn data is final
  chose  : writer-agent :: exec_memo (confidence=0.593)
  why    : writer-agent::exec_memo selected (score=0.593). Semantic match
           0.725 on tokens [an, draft, executive, memo]; tag overlap 0.200
           on [draft, memo]. Runner-up research-agent::web_research rejected:
           below min_score.
  features: min_score=0.050, confidence_required=0.500, score=0.593,
            semantic=0.725, tag_overlap=0.200
  alternatives:
    - writer-agent :: exec_memo  score=0.593 sem=0.725 tags=0.200
    - research-agent :: web_research  score=0.000  REJECTED (below min_score)
  agent self-caveats:
    - English only
    - max ~400 words per memo
    - escalate when: any quantitative claim cited without source
```

No LLM produced that. It's a template rendered from the features the router actually used. It's ASCII-safe so it prints on a default Windows console.

## How routing works

You cannot match a free-text task to a skill with literally zero model calls. But you can do it with **one cheap embedding lookup** instead of many rounds of supervisor reasoning — and that delta is the cost wedge.

The default scorer is a dependency-free TF-IDF over skill text. Good enough to demonstrate the win on first run, trivially swappable:

```python
from sentence_transformers import SentenceTransformer
from agent2society import Society

model = SentenceTransformer("all-MiniLM-L6-v2")
society = Society(embed_fn=lambda texts: model.encode(texts).tolist())
```

For every `(agent, skill)` pair, the router scores:

- cosine similarity of skill text vs task text, and
- a deterministic tag-overlap bonus

…then returns a ranked list. Conformance runs on the top candidate; on failure the society falls through to the next and records the rejected_reason on the loser — so the explanation tells you *why* the runner-up was passed over.

## Conformance — the guardrail

Before dispatch, agent2society answers two questions deterministically:

1. Does this agent's card actually declare this skill?
2. Is the task inside this agent's declared boundary (allow / deny)?

If either fails, the dispatch is blocked. `ConformanceViolation` is raised in strict mode, or recorded in telemetry + the explanation's `blocked_reason` field in non-strict mode.

```python
society.boundary("writer-agent", deny=["financial-data", "pii"])
society.boundary("research-agent", allow=["public"])
```

This is the layer that stops an agent from silently accepting work it never claimed it could do — a class of failure the supervisor pattern leaks by design.

## Handoff envelope — context that survives the chain

A bare string answers "what to do next". A `Handoff` answers "what to do next, why it exists, what's been done so far, what we assume to be true, and what would force a human review":

```python
h0 = Handoff(
    task="Research Q3 customer churn drivers",
    intent="prep the Q3 board pack",
    assumptions=["churn data through end-of-quarter is final"],
    confidence_required=0.5,
)
research_text = society.run(h0)

# Extend the handoff: the next agent sees the upstream decision in its
# `prior` chain (and so does the routing explanation).
h1 = h0.extend(
    agent="research-agent",
    skill="web_research",
    summary="found 3 churn drivers",
    confidence=0.62,
    next_task="Draft an executive memo on those churn drivers",
)
memo = society.run(h1)
```

## SelfAssessment — agents declare their own limits

A card can carry a `selfAssessment` block. agent2society surfaces those limits on every explanation that picks the agent, so callers and downstream agents see the same caveats the agent claims for itself:

```json
{
  "name": "writer-agent",
  "skills": [{"id": "exec_memo", "name": "Executive Memo"}],
  "selfAssessment": {
    "confidenceModel": "tfidf_score",
    "knownLimitations": ["English only", "max ~400 words per memo"],
    "outOfScope": ["legal opinion", "binding financial guidance"],
    "escalateWhen": ["any quantitative claim cited without source"]
  }
}
```

## Governance — detection, never auto-correct

```python
society.on_low_confidence(lambda exp: notify(exp), threshold=0.5)
society.on_human_review(lambda exp, result: page_oncall(result))
society.on_conflict(lambda c: log("conflict", c.detail))
society.on_capability_drift(lambda d: log("drift", d.agent))
```

- **on_low_confidence** — fires when a decision's confidence is below the handoff's `confidence_required` (or a society-wide `threshold=`).
- **on_human_review** — fires when a `Handoff.human_review_when(result_text)` predicate returns True.
- **on_conflict** — fires when the same task text was routed to different (agent, skill) pairs across handoffs in the window.
- **on_capability_drift** — fires when one agent is selected across an unusually broad spread of skills (a signal that skill descriptions may be too generic).

**Hooks are side effects.** They cannot block, retry, or modify a dispatch. Handler exceptions are caught (so a buggy hook can never break a dispatch) but logged at WARNING on the `agent2society` logger — they never vanish silently. The package never silently auto-corrects — that opacity is exactly what makes multi-agent systems untrustworthy.

## Production ops (v0.5)

Three knobs for running this in real systems.

**Metrics.** Every Society exposes a `MetricsCollector` with counters and summary-style histograms, ready to scrape.

```python
society.metrics.snapshot()           # JSON-serialisable dict
print(society.metrics.render_prometheus())   # Prometheus text format
```

Pre-registered series cover routes, dispatches, retries, failures, conformance blocks, conflicts, low-confidence events, drift, human-review firings, and optimizer applications.

**Persistent ledger.** Replace the default in-memory store with a JSONL file to keep decisions across restarts.

```python
from agent2society import Society, JsonlFileStore

society = Society(store=JsonlFileStore("audit.jsonl"))
```

Each `run()` appends one JSON line; a fresh `Society(store=JsonlFileStore("audit.jsonl"))` rebuilds the index on startup. Corrupt lines are skipped, not fatal.

**Auto-retry / fallback dispatch.** A transport error on the chosen candidate falls through to the next conformance-passing candidate.

```python
society.run(handoff, retry=True)
```

Every retry is recorded in `RoutingRecord.fallbacks` and emits a `dispatch_retries_total` counter.

**Thread-safety.** Mutation paths (`add`, `run`, `optimize`, `apply_optimization`) acquire a reentrant lock. Concurrent `run()` calls from multiple worker threads are safe.

**LLM-assisted optimizer.** Pass an optional `llm_fn=` to `society.optimize()` to let an LLM *propose* additional candidate tokens. Suggestions go through the same filters and backtest as discriminative tokens — the LLM never decides routing.

```python
def llm_proposer(missed_tasks, card, skill):
    return your_llm.suggest_tags(missed_tasks, card, skill)

report = society.optimize(labels, llm_fn=llm_proposer)
```

**Session traces (v0.5.3).** Join the telemetry sink and the explanation store into an ordered event stream per session — handy for dashboards, post-mortems, or piping into a notebook.

```python
from agent2society import SessionTracer

tracer = SessionTracer(society)
for ev in tracer.events():
    print(ev.handoff_id, ev.chosen_agent, ev.score, ev.flags)
```

No new per-route cost — `TraceEvent`s are built from data the Society was already recording.

## Native agents — adapters

Any of these objects can be dropped straight into `mesh.add(...)`:

- A CrewAI `Crew` (anything with `.kickoff()` and `.agents`)
- A compiled LangGraph (anything with `.invoke()` and `.nodes`)
- An AutoGen `ConversableAgent` or `GroupChatManager`
- Any plain callable, or any object with `run` / `invoke` / `kickoff`

Adapters expose a card built from the agent's metadata; the dispatcher routes locally without HTTP. Register your own:

```python
from agent2society.adapters.base import Adapter, register_adapter

class MyAdapter(Adapter):
    def matches(self, obj): ...
    def to_card(self, obj): ...
    def to_handler(self, obj): ...

register_adapter(MyAdapter())
```

## Telemetry

`mesh.report()` prints (and returns) a per-task ledger: chosen agent + skill, similarity score, any fallbacks, conformance violations, a token estimate, and the `mesh.explain(handoff_id)` hint so you can pull the full rationale on any line.

```text
agent2society report
============================================================
tasks=3  dispatches=3  violations=0  total_tokens~87

[1] [a77d6eed5a15] 'Draft an executive memo on customer churn'
    intent: prep the Q3 board pack
    -> writer-agent :: exec_memo (score=0.593)
    tokens: req~10 resp~16
    explain: mesh.explain('a77d6eed5a15')
```

## Benchmarks

Run the supervisor head-to-head:

```bash
python benchmarks/run.py
```

Typical output on the default 12-task suite:

```text
coordination tokens per task
  supervisor:  median=427 mean=427.8 min=424 max=433
  agent2society:      median=9   mean=9.4   min=7   max=15

reduction:         94.12%

task success (right (agent, skill) chosen)
  supervisor:  11/12
  agent2society:      11/12
```

Same correctness on a labelled suite, ~140x cheaper coordination. (Dispatch cost is held equal across both methods; the delta is purely the supervisor's reasoning tokens.)

## Examples

- [`examples/basic_mesh.py`](examples/basic_mesh.py) — three-agent mesh, conformance guardrail, explanation surface.
- [`examples/transparent_mesh.py`](examples/transparent_mesh.py) — SelfAssessment, handoff chain, low-confidence + human-review + conflict hooks.

## License

Apache-2.0.
