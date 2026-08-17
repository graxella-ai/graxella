# Disclosure Spec (v0.1)

**Status:** binding from Phase S onward · **Owner:** graxella core ·
**Implementation:** Phase 2, build plan task 2-3 · **L0 exists today:**
`graxella/mesh.py::_build_peer_context`

Tiered, router-driven progressive disclosure of agent capabilities. Kills
"waiting tokens" (supervisor LLMs re-reading every agent's full description on
every hop) and bounds peer-context cost so it stays **flat as the mesh grows**.
Targets MAST's inter-agent misalignment cluster (32.3% of observed failures):
agents coordinate better when each hop sees exactly the capability information
it needs — no less (withholding), no more (noise and cost).

## The principle: the router decides what is revealed

In OpenClaw-style skill systems, the *agent* decides what to load — token cost
depends on LLM behavior. In graxella, the *deterministic router* drives
disclosure: what gets revealed is a function of the routing shortlist, so token
cost is predictable, auditable, and independent of model whims. Disclosure
content is **compiled from evidence** (which details actually correlated with
successful handoffs) and ships as gated promotions — never hand-maintained
prose.

## The four tiers

| Tier | Content | Injected for | Trigger | Token budget (per agent) |
|---|---|---|---|---|
| **L0 — Directory** | name + one-line role | every peer, every call | always (the peer directory) | ≤ 25 tokens |
| **L1 — Skill summaries** | per-skill one-liners + tags + limitations line | router's top-k shortlist only | candidate ranking computed | ≤ 120 tokens |
| **L2 — Full contract** | goals, backstory/system context, mined exemplars (case recall), declared limitations, handoff expectations | the chosen agent only | dispatch decision made | ≤ 600 tokens |
| **L3 — Tool schemas** | full argument schemas, preconditions, healing notes | the executing agent, per tool | tool invocation | per tool, framework-native |

Rules:

1. **L0 is unconditional and bounded.** The peer directory is always present
   (agents must know who exists) and its total cost is `O(agents × 25 tokens)`.
   Beyond ~50 agents, L0 itself paginates by domain — the router's shortlist
   determines which domain pages are shown.
2. **L1 loads only for the shortlist.** Top-k is a router parameter (default
   k=3). Non-candidates never pay L1 cost.
3. **L2 loads only for the winner.** Exactly one agent per hop receives L2 —
   this is where case recall (build plan 0B-2) injects its past-episode block.
4. **L3 is invocation-scoped.** Tool schemas ride the framework's native tool
   binding; graxella adds healing annotations (promoted transforms) only for
   tools the agent actually holds.

## Content is compiled, not authored

- L0/L1 lines derive automatically from agent cards (goal, tool descriptions)
  at registration — the same sources `_describe_agent` uses today.
- From Phase 2 onward, a disclosure compiler (offline agenda job) refines L1/L2
  content from ledger evidence: details present in successful handoffs are
  kept; details never correlated with outcomes are dropped. Each refinement is
  a `Proposal(kind=disclosure_summary)` through the gate — see
  docs/specs/promotion-spec.md.
- Declared limitations in L1/L2 stay honest mechanically: the capability-drift
  and conformance detectors flag agents whose behavior diverges from their
  disclosed cards.

## Measurement (definition of done for task 2-3)

The disclosure layer records, per hop: tokens spent per tier, shortlist size,
and which tier content the outcome cites. The acceptance benchmark: total
peer-context token cost stays **flat (±10%)** as the mesh grows from 5 to 50
agents, while routing quality (score margin) does not degrade.

## Standards path (Phase 5, task 5-2)

The tier model is drafted as a public **A2A v1.0.1 extension**: L0/L1 map to
progressive AgentCard field revelation; L2 maps to a task-scoped capability
contract exchange. Graxella's implementation is the reference.

## Non-goals

- No LLM decides disclosure (that reintroduces waiting tokens).
- No hand-authored YAML/JSON capability files — cards derive from code and
  evidence (silent-plumbing principle).
