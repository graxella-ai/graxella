# agent2society on top of Google A2A — drop-in transparency layer

A real customer-support pipeline running on **Google's official `a2a-sdk` 1.1.0**, then run twice with the *same* agents:

1. **`a2a_only/`** — pure A2A protocol with a hand-rolled supervisor.
2. **`a2a_with_agent2society/`** — same A2A agents, with `pip install agent2society` on top.

The four A2A agent server files in [`agents/`](agents/) are **byte-identical** between runs. Only the orchestrator changes.

[![Open notebook](https://img.shields.io/badge/notebook-a2a__vs__agent2society.ipynb-blue)](a2a_vs_agent2society.ipynb) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE) [![PyPI](https://img.shields.io/pypi/v/agent2society)](https://pypi.org/project/agent2society/)

## What you get on top of A2A — without touching the agents

| Dimension                              | A2A only (hand-rolled supervisor) | A2A + agent2society       |
| -------------------------------------- | --------------------------------- | ------------------------- |
| Routing accuracy (20 tickets)          | 11/20 (55%)                       | **12/20 (60%)**           |
| Coordination tokens (LLM supervisor)   | 7,613                             | **0** (TF-IDF, no LLM)    |
| Total tokens                           | 10,690                            | **2,834** (-73.5%)        |
| Cost @ claude-opus-4 (this batch)      | $0.37                             | **$0.18**                 |
| Cost @ claude-opus-4 / 1k tickets      | $18.73                            | **$9.23**                 |
| Governance alerts surfaced             | 0 (invisible)                     | **17** (low-margin / PII) |
| PII tickets blocked pre-dispatch       | 0                                 | **2** (SSN + DOB)         |
| Per-decision rationale / margin        | none                              | every decision            |
| Audit trail                            | none                              | per-handoff `RoutingExplanation` |
| Orchestrator lines authored            | ~60                               | ~5                        |
| A2A agent files modified               | 0 (baseline)                      | **0 — purely additive**   |

Full output: [`comparison_output.txt`](comparison_output.txt).

## Quickstart

```bash
git clone https://github.com/sridhar-nomula/agent2society-a2a-demo.git
cd agent2society-a2a-demo
pip install agent2society a2a-sdk fastapi uvicorn httpx
python compare.py
```

`compare.py` boots all 4 A2A servers (real `FastAPI` + `uvicorn` + JSON-RPC) and runs 20 tickets through each orchestrator side by side.

Prefer a notebook? Open [`a2a_vs_agent2society.ipynb`](a2a_vs_agent2society.ipynb) — covers governance hooks, boundaries, `SessionTracer`, sentence-transformer routing, the Prometheus metrics export, and the `JsonlFileStore` audit log.

## Try it on your existing A2A stack

If you already have A2A agents, swap your supervisor for `Society()`:

```python
from agent2society import Society, Handoff

society = Society(strict=False, min_score=0.05)
for card in discover_cards_however_you_already_do():
    society.add(card)

# Detection-only governance hooks — they surface signal, never block dispatch
society.on_low_margin(lambda exp: log.warn(f"low-margin: {exp.chosen_agent} {exp.margin:.3f}"))
society.on_low_confidence(lambda exp: log.warn(f"low-conf:  {exp.chosen_agent} {exp.confidence:.3f}"))
society.boundary("KnowledgeBaseLookup", deny=["passport", "ssn", "date of birth"])

h = Handoff(task="What is your refund policy?")
reply = society.run(h)
exp = society.explain(h.id)            # rationale, margin, alternatives, flags
```

The transport adapter for the Google `a2a-sdk` 1.1.0 wire format is 30 lines — see [`a2a_with_agent2society/orchestrator.py`](a2a_with_agent2society/orchestrator.py).

## What the agents are

- **IntentClassifier** (port 8101) — labels the ticket: billing | technical | feedback | escalation
- **KnowledgeBaseLookup** (port 8102) — FAQ answers for refund policy, password reset, shipping, billing
- **EscalationHandler** (port 8103) — opens escalation cases for critical / repeated complaints
- **ResponseGenerator** (port 8104) — drafts polished customer-facing replies

Each is a real `AgentExecutor` subclass behind a FastAPI app, advertising itself via `/.well-known/agent-card.json` per the A2A spec.

## Test batch

20 tickets in [`tickets.py`](tickets.py): 10 standard (clear routing cases) + 10 stress (PII leakage, multi-intent, OOD nonsense, ambiguous wording). The stress batch is where the dimensions other than routing accuracy start to separate.

## The proof point

```bash
$ grep -l "from agents\|import agents" a2a_only/orchestrator.py a2a_with_agent2society/orchestrator.py
# (empty -- neither orchestrator imports agent code)
```

Both orchestrators talk to the agents **only over the A2A protocol** — discovery via `/.well-known/agent-card.json`, dispatch via JSON-RPC `SendMessage`. The agent server processes could be running on different machines, in a different language, owned by a different team. **agent2society sits *on top of* A2A, not *inside* the agents.**

## File layout

```
.
├─ agents/                          # 4 A2A agent server files (shared, identical)
│  ├─ intent_classifier.py
│  ├─ kb_lookup.py
│  ├─ escalation_handler.py
│  ├─ response_generator.py
│  └─ _runtime.py                   # shared FastAPI/uvicorn bootstrap
├─ tickets.py                       # 20 test tickets (10 standard + 10 stress)
├─ run_servers.py                   # boots all 4 in background threads
├─ metrics.py                       # token / cost / latency helpers
├─ a2a_only/
│  └─ orchestrator.py               # hand-rolled supervisor (discover -> route -> dispatch)
├─ a2a_with_agent2society/
│  └─ orchestrator.py               # Society(transport=...) wrapping discovered cards
├─ compare.py                       # runs both orchestrators, prints the table
├─ a2a_vs_agent2society.ipynb       # walkthrough notebook
└─ comparison_output.txt            # last measured run
```

## Calibration

- Token formulas in [`metrics.py`](metrics.py) — supervisor prompt is calibrated against a LangGraph-style 4-agent system prompt; execution tokens use the same formula as the legacy `comparisons/` benchmark.
- The "A2A only" routing logic is a keyword-overlap baseline. A production stack would use an LLM supervisor (LangGraph, hand-rolled GPT-4o routing, etc.) — the coordination-token row models that cost; the routing-accuracy row uses the keyword matcher actually shipped in the demo.
- Numbers regenerate end-to-end on every `python compare.py` — nothing is cached.

## License

MIT — see [`LICENSE`](LICENSE).

## Related

- agent2society on PyPI — https://pypi.org/project/agent2society/
- Google A2A protocol — https://google.github.io/A2A/
