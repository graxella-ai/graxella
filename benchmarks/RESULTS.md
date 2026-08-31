# Graxella benchmark results

Measured on a dev laptop (Windows 11, Python 3.13, graxella `.venv`).
LLM cost measured against **real Ollama `qwen2.5:3b`**, not simulated.
Every number here is reproducible with the command shown; a red number is
kept honest rather than hidden.

Last run: 2026-08-28.

---

## 1. Drift healing — the core thesis (`bench_healing.py`)

**Question:** a tool's schema drifts under a running agent (the field
`city` is removed; the live endpoint now wants `location`). Over 50
identical calls, what does each recovery strategy cost?

```
<venv>/python -X utf8 benchmarks/bench_healing.py 50
```

| Arm | Success | LLM calls | Total ms | p50 ms/call | p95 ms/call |
|---|---:|---:|---:|---:|---:|
| naive (no recovery) | 0.0% | 0 | 0.1 | 0.00 | 0.00 |
| llm_retry (re-ask the model every call) | 100.0% | **50** | **150,593** | **3006.20** | 3201.43 |
| graxella (heal-once, then deterministic) | 100.0% | **1** | **3,207** | **0.25** | 1.63 |

**Headline:** graxella vs the industry-standard `llm_retry`:
**98% fewer LLM calls (50 → 1)** and **47× lower total latency**, at the
same 100% success rate.

Why: the naive agent breaks on every drifted call (0% success). The
standard fix — re-ask an LLM to repair the arguments — works but pays a
full model round-trip *every call, forever* (~3.0 s each). Graxella pays
the LLM **exactly once** (heal-once proposes a `TransformRecipe`), then
every subsequent call is healed deterministically in **~0.25 ms** with
zero LLM. The per-LLM-call cost is identical across both arms (same model,
same repair prompt) — the only variable is *how many times you pay it*.

At production volume the gap compounds: at 10k drifted calls, `llm_retry`
pays 10k model round-trips; graxella still pays one.

> Note: both LLM-using arms route through the same `llm_repair_field_map`
> call, so this isolates the healing architecture, not model quality. With
> Ollama down, the harness substitutes a labelled fixed LLM latency so the
> shape still runs — but the numbers above are real Ollama round-trips.

---

## 2. Governed routing + gate latency (`perf_route.py`)

**Question:** at 1,000 registered agents, how fast is a full *governed*
dispatch (route + decision write + outcome write), and how fast is the
Evidence Gate's prior query over a warm ledger?

```
<venv>/python -X utf8 benchmarks/perf_route.py 1000 200
```

| Metric | Measured | Note |
|---|---:|---|
| `route()` p50 @ 1000 agents | **42.4 ms** | full governed dispatch incl. 2 ledger writes |
| `route()` p95 @ 1000 agents | 59.4 ms | |
| mesh build @ 1000 agents | **0.1 s** | was 38 s before the O(n²) deepcopy fix |
| `gate.prior()` p50 over 2000 outcomes | **0.08 ms** | post-refresh; target was < 5 ms |
| `gate.prior()` p95 | 0.10 ms | |

The gate — the memory-grounded Bayesian decision at the heart of
governance — resolves a prior in **under a tenth of a millisecond**, so
governance is never the bottleneck. Routing at 1k agents sits at ~42 ms
for the *entire* governed path (routing itself is a small fraction; the
rest is the two SQLite ledger writes that make the decision auditable).

---

## Environment / reproducibility

- Toolchain migrated to **langchain 1.3 `create_agent`** (the deprecated
  `langgraph.prebuilt.create_react_agent` is fully removed from graxella
  source and tests).
- Core test suite: **all tests pass** (3 skipped), no deprecation warnings
  from graxella code.
- Run the suite: `<venv>/python -m pytest -q` from `packages/graxella`.
