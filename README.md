<div align="center">

<img src="assets/logo.svg" alt="graxella" width="380">

**Accountable change-control for agent behavior.**

Your agents already work. Can you prove what they changed — and undo it if it was wrong?

[![ci](https://github.com/graxella/graxella/actions/workflows/ci.yml/badge.svg)](https://github.com/graxella/graxella/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](https://www.python.org)
[![license](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

</div>

---

## The problem

Traditional software governs behavior change through a whole discipline:
version control, review, CI gates, deploys, audit logs. Every change is
proposed, approved, recorded, and reversible.

Agents have none of that — yet their behavior mutates continuously:

- **A tool contract changes underneath them.** The carrier renames
  `order_id` to `tracking_ref`. Your agent starts asking customers for a
  tracking number it was supposed to look up itself.
- **They loop.** Two agents hand the same task back and forth until
  something runs out — usually your budget.
- **They "learn" invisibly.** Something works once, gets reused forever,
  and nobody approved it or can point at why.
- **There is no paper trail.** "Why did it do that?" has no answer that
  survives the chat scrollback.

The usual fix is a smarter prompt or a retry. That is expensive at
runtime, non-deterministic, and unauditable.

## What graxella does

It sits **underneath** your agents — you keep writing plain LangChain or
LangGraph — and turns behavior change into something with a process:

```
tool breaks  →  repaired once  →  cited proposal  →  you approve  →  permanent rule
                                                                          ↓
                                                    evidence turns bad → auto-demoted
```

**One rule explains the whole design: the LLM may propose; the evidence
decides.** Routing, promotion, demotion, and every verdict are
deterministic and recorded. A model appears in exactly one place — the
drift healer's proposal step — and even there its output is validated
against the real fallback before it is ever trusted twice.

## The code you actually write

```python
import graxella
from pydantic import BaseModel

grx = graxella.Session("support-desk", domain="support")

class TrackRequest(BaseModel):                 # the carrier's NEW schema
    tracking_ref: str                          # ...it used to be order_id

def carrier_v2(args: dict) -> str:
    req = TrackRequest(**args)                 # a real, validating client
    return f"parcel {req.tracking_ref}: out for delivery"

@grx.tool(fallback=carrier_v2)                 # <- the only graxella line
def track_shipment(order_id: str) -> str:
    """track a shipment's delivery status by order id"""
    return carrier_v2({"order_id": order_id})  # drifts: the old field name
```

That's it. `@grx.tool` returns a real LangChain `BaseTool`, so it drops
into `create_agent(llm, [track_shipment])` unchanged.

The first time the drift happens, graxella repairs it, caches the repair
as a deterministic recipe, and files a **cited proposal** for you:

```python
track_shipment.invoke({"order_id": "A-1042"})
# -> 'parcel A-1042: out for delivery'      the customer never saw a failure

grx.healer_calls          # 1  — repaired once, never again
grx.pending()             # 1  — nothing was promoted silently
print(grx.why(grx.pending()[0]))   # the cited reasoning behind the verdict
```

Approve it and it becomes a permanent rule. Later, if the evidence turns
against that rule, `grx.reconcile()` **demotes it on its own** — no human,
no LLM, just the posterior:

```
reconcile(): promoted=1  demoted=1
  demoted apr_581870be...: status=rolled_back
  reason='posterior 0.29 < 0.5 over 5 uses (1 ok / 4 failed)'
```

That last part — **un-learning** — is the piece most agent-memory systems
don't have. Anything can accumulate rules. Removing one on evidence is
what makes it change-control rather than a cache.

## Install

```bash
pip install graxella            # everything above works
pip install "graxella[heal]"    # + the built-in drift healer (DSPy/Ollama)
```

One command, no extras needed. Extras are only for things you might
genuinely not want: `[heal]` (a local model runtime for repairing
ambiguous drift), `[langgraph]` (the graph runtime, for the mesh
adapters and tutorials 08+), `[embed]` (local sentence-transformers),
`[otel]`, `[mcp]`.

Nothing calls out to a hosted service — the healer runs against a local
Ollama by default, and without one, drift fails **loudly** rather than
faking a repair.

## Measured, not asserted

Every number here comes from a script in this repo that you can run. The
runs are on small local models (`qwen2.5:7b`, `nomic-embed-text`).

| What | Result | Produced by |
|---|---|---|
| Routing across 15 paraphrased/slang tickets | **15/15** vs **13/15** for a hand-written keyword router | [tutorial 11 §A](tutorials/11_capstone_governed_org.ipynb) |
| A runaway two-agent handoff loop | stopped at **3 hops** + escalated, vs **20 hops burned** by a hand-rolled loop that never detects it | [tutorial 11 §B](tutorials/11_capstone_governed_org.ipynb) |
| Repairing a drifted tool | **1** healer call, ever — then a cached deterministic recipe | [tutorial 02](tutorials/02_self_healing.py) |
| Test suite | **285 passed, 3 skipped** | `uv run pytest` |
| Load-bearing claims, checked in CI | 5 probes | [`benchmarks/eval_harness.py`](benchmarks/eval_harness.py) |

**What these numbers are not:** single-run results on one small domain,
not a statistically powered benchmark. The CI scorecard exists so they
fail loudly when they stop being true.

## Learn it

[`tutorials/`](tutorials/) is a graded path — 01–06 need **no LLM at all**:

| # | Tutorial | You learn |
|---|---|---|
| 01–03 | [first tool](tutorials/01_first_tool.py) → [self-healing](tutorials/02_self_healing.py) → [review queue](tutorials/03_review_queue.py) | a plain function becomes governed; a drift heals once; a human approves it into a permanent rule |
| 04–06 | [mesh](tutorials/04_agent_mesh.py) · [recall](tutorials/05_memory_recall.py) · [audit](tutorials/06_audit_trail.py) | multi-agent routing with no routing-LLM, memory that recalls what worked, "why did it do that?" in one call |
| 07–08 | [LangChain](tutorials/07_langchain_agent.py) · [LangGraph](tutorials/08_langgraph_mesh.py) | your **real** agents, unchanged, governed underneath |
| 09–10 | [handoffs](tutorials/09_agent_handoff.py) · [supervisor team](tutorials/10_supervisor_team.py) | typed A2A handoffs, loops caught and escalated, a full org chart |
| **11** | **[capstone notebook](tutorials/11_capstone_governed_org.ipynb)** | **every layer on one hierarchical org — then the same org rebuilt with zero graxella, compared on tokens, hops, and failure modes** |

## Honest limits

This project's whole claim is accountability, so the limits are stated
rather than buried:

- **It does not make your model smarter, and it does not claim a lower
  hallucination rate.** Tutorial 11 contains a live probe where the
  governed agent hallucinated exactly as badly as the ungoverned one, and
  the built-in claim-detector missed it on both sides. That result is kept
  in the notebook. What differs is that the governed side's tool trail
  makes the false claim *checkable afterwards*.
- **Governance is detection-only.** graxella flags reasoning/action
  mismatches and constitution violations; it does not silently block or
  rewrite your agent's output.
- **`0.1.0`, alpha.** The API surface is small and tested, but it will move.
- **The drift healer needs a local model** (or your own `@grx.healer`).
  Without one, drift fails **loudly** — it never fakes a repair.

## Where it sits

Not a competitor to your agent framework — a layer under it.

| | Guardrails.ai | NeMo Guardrails | LangGraph alone | **graxella** |
|---|:---:|:---:|:---:|:---:|
| Validate a single output | ✅ | ✅ | — | ✅ |
| Repair a broken tool contract | — | — | — | ✅ |
| Evidence-gated promotion | — | — | — | ✅ |
| **Reverse a learned behavior** | — | — | — | ✅ |
| Cited audit trail per decision | — | — | — | ✅ |

Guardrails and NeMo answer *"is this one output acceptable?"*. graxella
answers *"what changed in my agent's behavior, who approved it, and can I
undo it?"* — a different question, and they compose fine.

## Docs

- [`docs/FIRST_CUT_SCOPE.md`](docs/FIRST_CUT_SCOPE.md) — the problem, and what this first cut does and doesn't claim
- [`docs/HEALING.md`](docs/HEALING.md) — the heal ladder, drift taxonomy, recipe capabilities
- [`docs/specs/`](docs/specs/) — the binding Promotion and Disclosure specs
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — setup, and the honesty contract for changes

## License

Apache-2.0 — see [LICENSE](LICENSE).
