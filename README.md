# Graxella

[![ci](https://github.com/sridharnomulas-maker/graxella/actions/workflows/ci.yml/badge.svg)](https://github.com/sridharnomulas-maker/graxella/actions/workflows/ci.yml)

**A graph-native intelligence layer for agent runtimes.**

Graxella turns every tool call your agent makes into a cited, replayable
`Episode`, mines those episodes offline for repeatable drift patterns,
lets a human review the proposals, and — once approved — silently
reroutes future calls at dispatch time. Zero LLM retries, zero
prompt-engineering, full W3C PROV-O audit trail.

```bash
git clone <this repo> && cd graxella && uv sync
```

---

## Why

Real agent systems fail the same way over and over:

- A tool gets deprecated. The agent keeps calling it. Every request burns
  a retry.
- A field gets renamed (`city` → `location`). The agent hasn't heard.
- A new API supersedes an old one. Nobody updates the prompt or the
  bindings.

The standard answer is "retry with a smarter prompt." That's expensive
at runtime, non-deterministic, and un-auditable. Graxella's answer is
different:

1. **Observe** every call (LangGraph, LangChain, AXON, or MCP-native).
2. **Mine** patterns offline — from lived experience *and* from docs.
3. **Propose** rules a human can approve or reject.
4. **Route** approved rules at dispatch time — deterministic, cited.

Compile-time LLM > runtime LLM. Detection-only governance. Audit-first
observability.

---

## Five-minute demo

```python
import graxella
from graxella import Rulebook
from graxella.agenda import Proposal

# Two tools: the legacy one (broken) and the current one.
@tool
def get_weather(city: str) -> str:
    raise RuntimeError("deprecated upstream")

@tool
def fetch_forecast(location: str) -> str:
    return f"forecast for {location}: sunny, 27C"

# One rule in the rulebook — the reviewer promoted it once.
rulebook = Rulebook(path="rulebook.json")
rulebook.promote(Proposal(
    kind="rule",
    subject="weather:get_weather->fetch_forecast",
    change={
        "if_intent": "weather",
        "replace_skill": "get_weather",
        "with_skill": "fetch_forecast",
        "recipe": {"field_map": {"city": "location"}},
    },
), approved_by="me")

# Wrap the tools — graxella now consults the rulebook before dispatch.
routed = graxella.wrap_tools([get_weather, fetch_forecast],
                             rulebook=rulebook, intent="weather")

routed["get_weather"]({"city": "Bengaluru"})
# -> "forecast for Bengaluru: sunny, 27C"   (no retry, no LLM roundtrip)
```

Full runnable version: [`examples/simple_langchain_demo.py`](examples/simple_langchain_demo.py).

---

## Package layout

Every submodule owns one concept — the API mirrors sklearn's flat
subpackage style.

```
graxella/
├── memory/          Experience episodes + durable stores        (Mnema)
├── knowledge/       Docs → typed KnowledgeSeed                  (ingest)
├── rulebook/        Approved substitutions on disk              (heal source of truth)
├── healing/         Drift → self-heal dispatch                  (BrownBrillion)
├── discovery/       Semantic tool catalog                       (AXON)
├── routing/         Deterministic destination picker            (agent2society)
├── reasoning/       Bottom-up Datalog engine + fact bridge
├── agenda/          Offline miners + hidden-agenda runner
├── audit/           W3C PROV-O JSON-LD export
├── integrations/    LangGraph callback · AXON adapter · MCP server
└── cli/             `graxella agenda run|review|promote|reject|audit`
```

Public API is re-exported from the top-level `graxella` package. Import
the submodule directly when you want the full surface (e.g.
`from graxella.agenda import DatalogMiner`).

---

## Install

Not on PyPI yet — install from source (uv workspace, all packages editable):

```bash
git clone <this repo> && cd graxella
uv sync                      # core: rulebook, gate, SQLite ledger, audit
uv sync --extra langgraph    # + LangGraph adapter
uv sync --extra mcp          # + MCP server binding
uv sync --extra heal         # + the built-in DSPy drift healer
uv sync --all-extras         # everything
```

(When the package ships to PyPI these become `pip install "graxella[...]"`.)

---

## Monorepo & development

This repository is a uv workspace:

```
packages/
├── graxella/        the runtime (this README's subject)
├── mnema/           immutable belief ledger (memory intelligence)
├── agent2society/   deterministic A2A routing + governance
└── axon-fabric/     tool-boundary fabric (B10 port lands Phase 2)
docs/specs/          binding specs — Promotion Spec, Disclosure Spec
docs/mast-tracking.md  failure-mode tracker (MAST, arXiv 2503.13657)
```

```bash
uv sync                                   # one venv, all packages editable
uv run pytest                             # graxella core suite
uv run pytest packages/mnema/tests        # sibling suites run separately
uv run pytest packages/agent2society/tests
```

## Roadmap

The build runs on the Evidence Loop plan: Phase 0 closes the
decision→outcome loop, Phase 1 lands the evidence-based promotion gate,
Phase 2 the multi-hop handoff runtime + tool fabric, Phase 3 the scale
substrate, Phase 4 the value ledger, Phase 5 the ecosystem adapters
(CrewAI, AutoGen, A2A). `docs/mast-tracking.md` tracks failure-mode
coverage per phase.

---

## License

Apache-2.0.
