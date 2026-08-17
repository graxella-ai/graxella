# MAST failure-mode tracking (build plan task S-6)

In-repo tracker for the [MAST Coverage Scorecard]. Each row is one of MAST's
14 peer-reviewed failure modes (arXiv 2503.13657) with graxella's status and
the build-plan task that moves it. **Process rule:** the tally must improve
at the exits of Phases 0, 1, and 2 — re-issue the scorecard at each phase
exit with this table as the source of truth.

> Migration note: when the GitHub remote exists, each OPEN/DESIGNED row
> becomes a labeled issue and this file links to them. Until then, this
> table is the tracker.

Statuses: **COVERED** (shipped) · **PARTIAL** (part shipped, rest phased) ·
**DESIGNED** (named mechanism + phase, unbuilt) · **GAP** (no mechanism yet).

| ID | Failure mode (freq.) | Status | Mechanism / gap | Build task |
|---|---|---|---|---|
| FM-1.1 | Disobey task specification (11.8%) | PARTIAL | Constitution invariants shipped; typed outcome verification pending | 0A-1, 0A-2 |
| FM-1.2 | Disobey role specification (1.5%) | COVERED | Capability cards + conformance + drift detector (agent2society) | — |
| FM-1.3 | Step repetition (15.7% — most common) | DESIGNED | Trajectory loop detection + budgets | 2-1 |
| FM-1.4 | Loss of conversation history (2.8%) | COVERED | Mnema externalized state; auto-wiring lands Phase 0 | 0A-2, 0B-2 |
| FM-1.5 | Unaware of termination conditions (12.4%) | DESIGNED | Termination invariants + completion checks | 2-1 |
| FM-2.1 | Conversation reset | PARTIAL | Handoff envelope + explanation store shipped; trajectory persistence pending | 2-1 |
| FM-2.2 | Fail to ask for clarification | COVERED | Low-confidence / low-margin detectors → human review | — |
| FM-2.3 | Task derailment | PARTIAL | Routing audit shipped; trajectory checks pending | 2-1 |
| FM-2.4 | Information withholding | PARTIAL | Peer directory (L0) shipped; shared namespaces pending | 2-3, 3-5 |
| FM-2.5 | Ignored other agent's input | **GAP** | No detector; design owed in Phase 2 spec (hop-input vs action diff) | 2-1 (prereq) |
| FM-2.6 | Reasoning–action mismatch | DESIGNED | Intent-vs-action mismatch miner (capture already shipped) | 1-7 |
| FM-3.1 | Premature termination | DESIGNED | Completion scoring on outcomes + trajectory close checks | 0A-1, 2-1 |
| FM-3.2 | No or incomplete verification | DESIGNED | Structural: every dispatch auto-records a typed outcome | 0A-2 |
| FM-3.3 | Incorrect verification | DESIGNED | Evidence-graded verification: provenance diversity, multi-signal fusion, cited verdicts | 1-2, 1-6 |

**Tally (2026-08-17): 3 covered · 4 partial · 6 designed · 1 gap.**

Detection-rate columns are added when the MAST-Data replay harness lands
(task 2-8) — replay numbers are labeled *would-have-detected on foreign
traces*, never live prevention rates (Step 3 of the scorecard's regression
plan earns that claim).

[MAST Coverage Scorecard]: https://claude.ai/code/artifact/2aafe16f-f501-48ec-8a31-e7cd3ee0d4f2
