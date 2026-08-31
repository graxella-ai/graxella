# graxella tutorials

Eleven runnable lessons that teach the whole surface -- ending where you
live: **native LangChain / LangGraph agents, governed**, then one
notebook that runs the whole stack at once and compares it against
having none of it.

Four tiers:

* **01-06 -- the fundamentals.** Self-contained, 2-4 minutes each,
  **no LLM and no API key required** (05 auto-upgrades to dense semantic
  recall if a local [Ollama](https://ollama.com) with `nomic-embed-text`
  is running, and falls back gracefully without it).
* **07-08 -- real agents.** Your actual `langchain.agents.create_agent`
  code, unchanged, with graxella governing underneath. These two need a
  local Ollama with `qwen2.5:3b` (they check, and exit politely if it's
  missing).
* **09-10 -- multi-agent orgs.** Typed A2A handoffs, loop containment,
  and a full supervisor-with-team org (`qwen2.5:7b`).
* **11 -- the capstone.** A Jupyter notebook: every layer above, on one
  hierarchical org, ending with the same org rebuilt with zero graxella
  and battled on identical tickets. See its own row below for what it
  needs.

```bash
# from the repo root
python tutorials/01_first_tool.py
```

| # | Tutorial | You learn | Time |
|---|----------|-----------|------|
| 01 | [first_tool](01_first_tool.py) | `Session` + `@grx.tool` -- a plain function becomes a governed tool; every call lands in the evidence ledger | 2 min |
| 02 | [self_healing](02_self_healing.py) | `@grx.tool(fallback=...)` -- an API drift is healed **once**, cached as a deterministic recipe, and queued for review | 3 min |
| 03 | [review_queue](03_review_queue.py) | `pending()` / `why()` / `approve()` -- an approved fix becomes a permanent rule; a fresh session handles the same drift with **zero** healer runs | 3 min |
| 04 | [agent_mesh](04_agent_mesh.py) | `grx.mesh([...])` -- multiple agents, deterministic semantic routing (no routing LLM), bounded multi-hop trajectories | 4 min |
| 05 | [memory_recall](05_memory_recall.py) | `similar_cases()` / `recall()` -- a differently-worded task recalls verified past experience | 3 min |
| 06 | [audit_trail](06_audit_trail.py) | `why()` / `touching()` / `provenance()` -- "why did it do that?" and "what touched this entity?" answered in one call each | 3 min |
| 07 | [langchain_agent](07_langchain_agent.py) | a **native `create_agent` agent** whose tool drifts mid-conversation and self-heals -- you wrote no healer; the fix awaits your review | 8 min* |
| 08 | [langgraph_mesh](08_langgraph_mesh.py) | several **native LangGraph agents** in one `grx.mesh()` -- deterministic routing, peer awareness, one evidence ledger | 10 min* |
| 09 | [agent_handoff](09_agent_handoff.py) | **agent-to-agent handoffs**: the typed `HANDOFF:` envelope, every hop audited -- and a runaway A2A loop caught, stopped, and escalated | 4 min |
| 10 | [supervisor_team](10_supervisor_team.py) | **a supervisor LLM over a team of specialists**, each with its own toolbox -- a carrier API breaks mid-shift, the customer never notices, the operator gets a cited fix to review. The supervisor only *nominates*; dispatch rides the A2A runtime underneath (typed Handoff envelopes + persisted routing explanations + deterministic fallback) | 12 min* |
| 11 | [capstone_governed_org](11_capstone_governed_org.ipynb) | **the whole stack at once, then the honest comparison**: prompt-insensitive routing, A2A handoffs + loop containment, both heal-ladder rungs (one fails live with the default model, gets fixed with a stronger `healer=` override), promote/reuse/un-learn, progressive disclosure -- and finally the SAME org rebuilt with zero graxella, battled ticket-for-ticket on tokens, hops, wallclock, and a reasoning-action-mismatch probe | 15-20 min* |

\* 07-08 need a local Ollama with `qwen2.5:3b`; 10-11 use `qwen2.5:7b`
(a team-of-agents org wants the slightly stronger model -- the tutorials
explain why); 11 also needs `deepseek-r1:latest` (one drift is
ambiguous enough that the default healer gets it wrong live, and the
notebook keeps that failure in) and `nomic-embed-text` (routing). 09
runs without any LLM.

## The arc

The tutorials walk the same loop graxella runs in production:

```
write a plain tool             (01)
   it drifts -> heals once     (02)
   a human approves            (03)   -> the fix is a permanent, cited rule
scale to many agents           (04)
   they remember what works    (05)
   and everything is           (06)   -> auditable, both directions
then bring your REAL agents:
   native LangChain agent      (07)   -> governed, healed, reviewed
   native LangGraph mesh       (08)   -> routed + ledgered, zero routing-LLM
   agents handing off to       (09)   -> typed handoffs; loops caught,
   each other                            stopped, and escalated
   a supervisor over a team    (10)   -> the real org chart, governed:
                                          every pick, call, and repair
                                          on one evidence ledger
then the whole stack at once,
   with graxella vs. without   (11)   -> one notebook, one org, every
                                          layer above -- then rebuilt
                                          with zero graxella and battled
                                          on the same tickets
```

The punchline of 07-08 is the whole thesis: **your agent code stays pure
LangChain/LangGraph** -- graxella appears only around the tools and the
mesh call, and that is enough to get healing, review, routing, memory, and
audit.

The one design rule underneath all of it: **the LLM may propose (a repair,
once); the evidence decides.** Routing, promotion, recall injection, and
every governance verdict are deterministic and recorded -- which is why the
ledger, not a prompt, is the source of truth.

## After the tutorials

- `examples/` -- end-to-end demos, including healing driven by a **real
  local LLM** (`society_autoheal_demo.py`), memory-grounded learning
  (`mnema_learning_demo.py`), and autonomous evidence-based promotion.
- `benchmarks/` -- measured numbers (real Ollama, honest reds kept).
- `docs/specs/` -- the binding Promotion and Disclosure specs.
