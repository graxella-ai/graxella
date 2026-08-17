# graxella showcase

Four demos that walk through what a graxella runtime does. The first
three run against **zero external services** (mock apps + plain-callable
agents). Showcase 04 plugs in a real LangGraph + Ollama LLM to prove the
same mechanics wrap a real workload.

## Prerequisites

Create a venv at the graxella package root and install pinned deps:

```bash
cd graxella
python -m venv .venv
# Windows:
.venv/Scripts/python -m pip install -r requirements.txt
# macOS/Linux:
.venv/bin/python     -m pip install -r requirements.txt
```

Then invoke every showcase script through the venv interpreter:

```bash
.venv/Scripts/python showcase/01_hello_wrap.py    # Windows
.venv/bin/python     showcase/01_hello_wrap.py    # macOS/Linux
```

### Extra prereqs for showcase 04 (real LLM)

Only needed for `04_langgraph_real_llm.py`:

```bash
# 1. install optional LLM extras
.venv/Scripts/python -m pip install -r requirements-llm.txt   # Windows
.venv/bin/python     -m pip install -r requirements-llm.txt   # macOS/Linux

# 2. pull the model and start Ollama
ollama pull qwen2.5:3b
ollama serve      # daemon must be listening on localhost:11434
```

## The five demos

| # | Script | What it proves | How to run |
|---|--------|----------------|------------|
| 01 | [01_hello_wrap.py](01_hello_wrap.py) | one call to `instrument()` attaches memory + society + tracer + gate + constitution to an unchanged app | `python showcase/01_hello_wrap.py` |
| 02 | [02_full_runtime.py](02_full_runtime.py) | the full six-step story: wrap → route → dispatch → constitution violation → outcomes → gate scoring → cross-source `why()` | `python showcase/02_full_runtime.py` |
| 03 | [03_dashboard.py](03_dashboard.py) | the operator dashboard: browser UI over the same runtime, with approve/reject/auto-evaluate buttons | `python showcase/03_dashboard.py` then open `http://127.0.0.1:8787/` |
| 04 | [04_langgraph_real_llm.py](04_langgraph_real_llm.py) | same six-step story on a **real** Ollama qwen2.5:3b LLM — mix of native `create_react_agent(...)` and `graxella.Agent(...)` in one `graxella.mesh([...])`, deterministic routing | `python showcase/04_langgraph_real_llm.py` |
| 05 | [05_travel_bot.py](05_travel_bot.py) | a realistic **travel booking bot** (flights + hotels) — 5-turn conversation, 4 tools, 2 agents (one native LangGraph, one `graxella.Agent`), and every graxella superpower (routing, memory, peer-awareness, constitution, gate, `why()`) comes for free | `python showcase/05_travel_bot.py` |

## The narrative

### 01 — the wrap is a one-liner

```python
wrapped = instrument(my_app, memory=memory, society=society)
wrapped.invoke({"input": "hello"})   # passes straight through
```

`my_app` is unchanged. `wrapped` is a facade — `.invoke`/`.stream`/`.batch`
still work, and now `.memory`/`.tracer`/`.gate`/`.constitution` are
available for queries.

### 02 — the full loop in one script

Prints six numbered sections:

1. **WRAP** — three fake agents registered with the mesh.
2. **ROUTE + DISPATCH** — four tasks routed deterministically, each
   agent's callable actually invoked via LocalTransport.
3. *(above; combined into 2)*
4. **CONSTITUTION** — invariant `delegate.no_frozen_agents` fires when
   the `writer` agent is picked. **The routing still succeeds**; the
   violation surfaces through the tracer for audit. Detection-only,
   never blocking.
5. **GATE** — three proposals with objective vectors:
   - narrow blast + high quality + full compliance → **AUTO_APPROVE**
   - wide blast (same scores otherwise) → forced to **NEEDS_HUMAN**
   - compliance below the 0.9 floor → **AUTO_REJECT**
6. **WHY** — cross-source provenance: 2 tracer events for the decision +
   full mnema keys (assertion, provenance, superseded_by, derived_rules,
   derived_skills, wal_events).

Ends with a unified tracer dump showing `beliefs`, `society`, `gate`,
and `constitution` events interleaved by monotonic sequence number.

### 03 — an operator can drive it from a browser

`03_dashboard.py` boots the same runtime under FastAPI + uvicorn at
`http://127.0.0.1:8787/`. Three panels auto-refresh every 5s:

- **Pending proposals** — with `approve` / `reject` / `auto-evaluate`
  buttons wired to `POST /gate/proposals/{id}/...`.
- **Recent tracer events** — the unified stream, source-tagged.
- **Constitution violations** — each linked to the `decision_id` that
  triggered it.

Also available:
- `http://127.0.0.1:8787/docs` — Swagger auto-docs of every route.

### 04 — one wrap over any agent shape

**Developer's choice of agent syntax.** graxella accepts native
LangGraph agents, its own CrewAI-shaped `Agent(...)`, and plain
`crewai.Agent` objects — all in the same `mesh([...])` list, with the
same governance and memory underneath. The whole thesis: silent
plumbing (routing, memory, peer-awareness, governance) is graxella's
job, so the developer just declares agents.

**Path A — native LangGraph:**

```python
from langgraph.prebuilt import create_react_agent
triage = create_react_agent(llm, tools=[check_order, lookup_policy], name="triage")
```

**Path B — graxella.Agent (CrewAI-shape, same wire under the hood):**

```python
responder = graxella.Agent(
    role="Customer Responder",
    goal="draft empathetic email replies",
    backstory="You draft short, warm emails.",
    tools=[draft_email],
    llm=llm,
)
```

**Path C — mix them freely:**

```python
app = graxella.mesh(
    [triage, responder, some_crewai_agent],   # any mix
    memory=graxella.Memory.sqlite("./mnema.db", agent_id="support"),
    constitution=constitution,
    gate=gate,
    router="tfidf",   # or "transformer", or a callable
)

out = app.invoke({"messages": [("user", "refund order 1234, arrived damaged")]})
print(out["route"])       # {"agent": "triage", "score": 0.31, "strategy": "tfidf", ...}
```

**Two dispatch flavours in the same module:**

| Helper | Routing strategy | When to use |
|--------|-----------------|-------------|
| `graxella.mesh([...])` | deterministic — TF-IDF or a small transformer | fast, auditable, zero-LLM cost per routing decision (default) |
| `graxella.supervisor([...], model=llm)` | supervisor LLM picks next agent | matches the `langgraph-supervisor` idiom when you want LLM reasoning about handoffs |

**Router knob** — swap the scoring backend without changing anything else:

| `router=` | Backend | Deps |
|-----------|---------|------|
| `None` / `"tfidf"` (default) | word-bag TF-IDF | none — always works |
| `"transformer"` | MiniLM (`all-MiniLM-L6-v2`) | `pip install sentence-transformers` |
| `"BAAI/bge-small-en-v1.5"` (or any ST model name) | that specific transformer | `sentence-transformers` |
| any `Callable[[str], list[float]]` | your own embedder | your responsibility |

All routers cost zero LLM tokens per decision. The transformer path
downloads ~90 MB weights on first use, then runs locally. On Windows,
you may need to enable long-path support before installing torch —
`sentence-transformers` pulls in torch and its nested extras can exceed
Windows' 260-char MAX_PATH.

**What graxella does silently** (the whole thesis — no JSON schemas,
no handoff protocols, no A2A envelope building for the user):

- Introspects each agent's bound tools -> capability index.
- Builds a peer directory and prepends it as a system message on every
  LLM call, so agents "know each other's limitations" without the
  developer wiring handoff prompts.
- Records every routing decision + tool call + outcome into mnema
  (immutable, cited, replayable).
- Runs constitution invariants on every delegation, detection-only —
  never silently rewrites the agent's decision.
- Scores learning proposals via the gate before any graph mutation.

The script walks the same six-step story as showcase 02, but every
routed task drives a real LLM invocation. Constitution + gate + `why()`
provenance fire identically to the mock demos — which is the point.

## What this showcase does NOT prove

- **CrewAI / AutoGen dedicated adapters**: duck-typing already handles
  `crewai.Agent` in a `graxella.mesh([...])` list. A dedicated
  `instrument(crewai.Crew)` adapter that also wraps the crew's own
  process is future work.
- **No load / adversarial testing**: single-process, happy-path only.
- **No OpenTelemetry export yet**: tracer events are in-memory +
  visible in the dashboard, but Jaeger/OTLP export is Beat 2 step 2.
