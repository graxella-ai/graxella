# B8 AXON — archive extraction (build plan task S-5)

**Status:** B8 (`B8_Enterprise_MCP_Orchestration/`, sibling folder outside this
repo) is **archived R&D**. Per the build plan rule, no future task references
B8 *code* — only this document. The runnable successor to every B8 idea is
either already in this repo or lands with a named Phase 2 task.

## What B8 was

The original AXON prototype (pre-B10): an MCP orchestration layer with
semantic tool discovery over a knowledge graph (FAISS + NetworkX), an MCP
gateway/registry, a decision memory with outcome learning, an orchestration
engine, a synthesis step, and five mock enterprise tools (CRM, finance, HR,
support, analytics).

## Disposition of each concept

| B8 module | Concept | Where it lives now |
|---|---|---|
| `graph/knowledge_graph.py` | Tool knowledge graph + semantic discovery | **Carries forward** → axon-fabric, Phase 2 task 2-6: capability graph with evidence-weighted edges (live cited trust scores) and shortest-path failover. The graph idea survives; the FAISS index is replaced by the workspace embedder adapters. |
| `mcp/gateway.py`, `mcp/registry.py` | MCP gateway + tool registry | Superseded by B10's federated schema registry (sqlite-vec), which ports in task 2-4. Gateway *plumbing* (federation, authN/Z proxying) is explicitly out of scope per the charter — delegated to the gateway ecosystem. |
| `memory/decision_memory.py` | Decision memory with outcome learning | Superseded by **mnema**: typed immutable assertions with provenance beat ad-hoc decision rows. The outcome-learning intent became the Evidence Loop itself. |
| `orchestration/engine.py` | Orchestration engine | Superseded by `graxella.mesh` / `Society` today, and the Phase 2 trajectory runtime (task 2-1) for multi-hop. |
| `synthesis/synthesizer.py` | Response synthesis | Superseded by the agent runners (LangGraph react loop) — synthesis belongs to the framework layer graxella wraps, not to graxella. |
| `tools/*.py` (CRM, finance, HR, support, analytics) | Mock enterprise tools | **Reusable as demo fixtures** for Phase 2 scenarios — the one part of B8 worth lifting near-verbatim when the trip-planner-class demo needs an enterprise flavor. |
| `data/generator.py`, `data/store.py` | Synthetic data + store | Superseded; regenerate per-demo as needed. |

## The two ideas that must not be lost

1. **Tools form a graph, not a list.** B8's core insight — edges like
   `supersedes`, `same_capability_as`, `version_of` between tools — is the
   backbone of Phase 2's trust-scored capability graph.
2. **Decisions deserve memory.** B8's decision-memory instinct, upgraded with
   immutability and provenance, became mnema and then the Evidence Loop.

B8's decks (`AXON_Architecture_Deck.pptx`, `AXON_Vision_Document.docx`) remain
in the archive folder for narrative history.
