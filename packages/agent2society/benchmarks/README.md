# agent2society benchmark

> Tokens-to-completion, agent2society routing vs. native supervisor routing,
> same task, two agents on two different frameworks.

Two runners ship in this directory:

1. **`run.py`** — single-shot routing benchmark, agent2society vs. a generic
   in-house supervisor with the same prompt shape a LangGraph supervisor
   would use. Twelve labeled tasks, six agents. ~94% token reduction
   at ~140x cost ratio.

2. **`run_langgraph.py`** — multi-step head-to-head against a *real*
   `langgraph.graph.StateGraph` supervisor with a counted fake chat
   model. Three complex scenarios (4-step / 2-step / 5-step pipelines).
   **~96% token reduction at ~200x cost ratio.** See "Head-to-head"
   below for the full numbers.

This is the gate metric. If agent2society completes the task for meaningfully
fewer coordination tokens with **equal or better task success**, there's
a real package and a publishable result. If the gap is marginal, we've
learned it in weeks rather than months.

## What we measure

For each task in a labeled suite:

| Metric                  | agent2society                                         | Native supervisor                                            |
| ----------------------- | ----------------------------------------------- | ------------------------------------------------------------ |
| Coordination tokens     | one embedding of the task (query side only)     | full chat prompt (system listing every card) + JSON decision |
| Dispatch tokens         | identical (same handler runs in-process)        | identical                                                    |
| Task success            | did the right `(agent, skill)` get chosen?      | same                                                         |
| One-time corpus embed   | sum of skill texts × embedding tokens, once     | n/a                                                          |

The headline number is the **median per-task coordination tokens**, and
the **total cost ratio** in USD given commodity pricing
(`gpt-4o-mini` chat vs `text-embedding-3-small`).

## Why this comparison is honest

1. **Same mesh, same handlers, same dispatch.** Both methods run through
   the same `LocalTransport`. Dispatch cost is identical and excluded
   from the headline.
2. **Correctness held equal.** The default mock supervisor uses the same
   TF-IDF scorer agent2society uses, so both methods route correctly on the
   labeled suite. The benchmark is "cost, holding correctness equal" —
   not "agent2society is more accurate than your LLM." (A real LLM at scale
   is usually accurate enough on simple routing; the cost is the issue.)
3. **The supervisor prompt is realistic, not strawman.** It lists every
   agent and every skill with description and tags, exactly the shape
   a developer would actually deploy. Token cost scales linearly with
   mesh size — which is the cost agent2society avoids by precomputing the
   skill index.
4. **Real-LLM mode is one swap away.** Pass `chat_fn=` (any callable
   that hits your provider) to the `Bench` and the harness will trust
   whatever `usage` the provider reports.

## Running it

```bash
# Default (mock supervisor, deterministic, no API keys needed)
python benchmarks/run.py

# For accurate token counts, install tiktoken:
pip install "agent2society[bench]"
```

To run against a real LLM, edit `benchmarks/run.py` and pass a `chat_fn`
to `Bench(...)`. The docstring at the top of `run.py` shows the OpenAI
SDK shape; any provider works as long as the callable returns
`{"content": "<json>", "usage": {...}}`.

## Reading the output

```text
agent2society vs. native supervisor benchmark
============================================================
tokenizer: tiktoken    tasks: 12

coordination tokens per task
  supervisor:  median=812 mean=812.3 min=807 max=820
  agent2society:     median=11  mean=11.5 min=9  max=15

totals
  supervisor total:  9748
  agent2society total:     302  (corpus=164, queries=138)
  reduction:         96.90%

estimated coordination cost (USD)
  supervisor:  $0.001372
  agent2society:     $0.000006
  ratio:       228.7x

task success (right (agent, skill) chosen)
  supervisor:  12/12
  agent2society:     12/12
```

The two numbers to quote are **reduction** (relative) and **task success
parity** (the equal-or-better condition). If you can hold one and improve
the other, the package is real.

## Head-to-head: real LangGraph supervisor

`run_langgraph.py` runs the same three complex multi-step scenarios
through:

* a real `langgraph.graph.StateGraph` supervisor with a counted fake
  chat model — coordination tokens come from tokenising the actual
  prompts LangGraph sends to the model on every step;
* agent2society, given the pre-decomposed sub-tasks. v1 agent2society is single-shot
  routing; the planner is out of scope. The benchmark is transparent
  about this — see the "Honest design tradeoff" section below.

### Latest run (default scenarios, gpt-4o-mini-shaped pricing)

```text
agent2society vs. real LangGraph supervisor (multi-step)
================================================================
tokenizer: tiktoken    scenarios: 3    total steps: 11

coordination tokens (sum across scenarios)
  langgraph supervisor:  7530  (14 supervisor LLM calls)
  agent2society:               293  (corpus=189, queries=104)
  reduction:             96.11%

estimated coordination cost (USD)
  langgraph:  $0.001174
  agent2society:    $0.000006
  ratio:      200.4x

step success (right agent in right order)
  langgraph:  11/11
  agent2society:    9/11
```

### Per-scenario

| Scenario             | Steps | LangGraph (calls / tokens / correct) | agent2society (tokens / correct) |
| -------------------- | ----- | ------------------------------------ | -------------------------- |
| `quarterly_brief`    | 4     | 5 / 2719 / 4                         | 36 / 2                     |
| `legal_intake`       | 2     | 3 / 1362 / 2                         | 25 / 2                     |
| `enterprise_launch`  | 5     | 6 / 3449 / 5                         | 43 / 5                     |

### How to read the two misses

agent2society lost 2 of 4 steps on `quarterly_brief`:

* `"Compute correlations between churn and customer segments..."` →
  agent2society routed to `translator-agent` (top score 0.119) over the
  expected `analyst-agent` (0.108). The TF-IDF default scorer can't
  separate "correlations" from "translate" on this short skill text.
* `"Translate the executive memo into Spanish"` → agent2society routed to
  `writer-agent::exec_memo` (0.358) because "executive memo" dominates
  the lexical signal even though "Translate" is the verb.

Both close with `Mesh(embed_fn=real_embedder)` — that's the whole
reason the embedder is pluggable. We surface these misses rather than
gaming the labeled suite to flatter TF-IDF.

### Honest design tradeoff

agent2society is given the pre-decomposed sub-tasks; LangGraph's supervisor
figures the decomposition out itself. That's the v1 tradeoff: agent2society
does not include an LLM planner, deliberately. The headline is:

* Per *step*, agent2society pays ~10 tokens; LangGraph pays ~500–1000+ tokens
  with a growing prompt.
* The cost gap is structural, not a quirk of the test setup — it comes
  from "LLM reads every card every step" vs "one embedding query".

The cases where this tradeoff favours LangGraph are precisely the ones
where workflow shape is genuinely unknown at design time. The cases
where it favours agent2society are the ones where you know the workflow and
just want it routed cheaply.

### Reproducing

```bash
pip install langgraph langchain-core tiktoken
python benchmarks/run_langgraph.py
```

To run against a real LLM end-to-end, swap `LangGraphSupervisor`'s
fake chat model for a real LangChain chat client (e.g.
`langchain_openai.ChatOpenAI`) and the same harness will read the real
`response_metadata` token counts.
