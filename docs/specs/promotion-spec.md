# Promotion Spec (v0.1)

**Status:** binding from Phase S onward · **Owner:** graxella core · **Canonical models:** `graxella/gate/spec.py`

Every learnable artifact in graxella — a compiled prompt, a healing transform, a
routing weight, a tool binding, a playbook delta, a model-tier assignment —
ships through **one** typed lifecycle: it is proposed, graded on evidence,
gated, promoted as a versioned artifact, and rolled back or superseded the same
way. This document defines that one shape. If a new feature wants to change
agent behavior and its change does not fit this schema, the schema is extended
here first — never bypassed.

## Why one schema

The v0.1 codebase had three parallel approval systems: `graxella.agenda.Proposal`
(miner output), `graxella.gate.promoter.Proposal` (scored gate queue), and
`Rulebook.promote` (healing rules). Three pipelines means three audit trails,
three review UIs, and three chances for a behavior change to ship ungated.
The Evidence Loop requires exactly one: **all behavior change is a Promotion.**

## The schema

One frozen pydantic model, `graxella.gate.spec.Proposal`:

| Field | Type | Meaning |
|---|---|---|
| `id` | str | `prop_<hash>` — deterministic for miner output (same evidence ⇒ same id), random for operator drafts |
| `spec_version` | str | this document's version, stamped on every instance |
| `kind` | `ArtifactKind` | what the artifact is (see kinds table) |
| `target` | `TargetScope` | where it applies: domain (required) + optional agent / skill / tool / `model_id` |
| `payload` | dict | the artifact content, shaped per kind (see kinds table) |
| `origin` | str | who produced it: `miner:<name>` or `operator:<name>` |
| `blast_radius` | `narrow \| wide \| unknown` | safety envelope, not a score — wide requires overwhelming same-tuple evidence or human sign-off |
| `evidence` | tuple[`EvidenceCitation`, ...] | citations into the ledger; **required** to reach APPROVED/ACTIVE |
| `confidence` | float 0..1, optional | miner's monotone signal strength; advisory only, never a gate criterion by itself |
| `status` | `ProposalStatus` | lifecycle state (see state machine) |
| `version` | int | monotone per (kind, target) chain |
| `supersedes` | str, optional | id of the promotion this replaces |
| `rollback_of` | str, optional | id of the promotion this reverts |
| `created_at` / `decided_at` / `decided_by` / `note` | audit metadata | who decided, when, why |

### The gate tuple

The Evidence Gate's prior lookup key is:

```
(target.domain, kind, target-specific scope, target.model_id)
```

`model_id` is part of the tuple by design — this is what makes graxella
LLM-agnostic: swap the model and learned behavior re-validates against fresh
evidence for the new tuple instead of silently misfiring.

## Artifact kinds

| `ArtifactKind` | Payload shape (informative) | Producer |
|---|---|---|
| `prompt` | `{instructions, optimizer, trainset_ref, baseline_metric, new_metric}` | DSPy/GEPA miner (Phase 4) |
| `transform` | `TransformRecipe` fields: `{field_map, static_defaults, drop_fields}` or verified JSONata | healer→compiler→verifier (Phase 2) |
| `route_weight` | `{intent, agent, weight_delta, window}` | CapabilityReweigher |
| `tool_binding` | `{replace_skill, with_skill, if_intent}` | RuleDistiller / drift healing |
| `playbook` | `{delta_items: [...]}` — append-only ACE-style deltas, never full rewrites | playbook miner (Phase 4+) |
| `model_tier` | `{task_kind, tier, provider_model, guardrail_metric}` | tier miner (Phase 4) |
| `disclosure_summary` | `{level, content, token_cost}` | disclosure compiler (Phase 2) |
| `rule` | legacy rulebook substitution — migrates to `tool_binding` | legacy |
| `trust_tier` | `{agent, tier}` | TrustPromoter |
| `skill_tags` | `{agent, skill_id, add_tags, drop_tags, backtest_ref}` | B11 optimizer |

## Lifecycle

```
PENDING ──► NEEDS_HUMAN ──► APPROVED ──► ACTIVE ──► SUPERSEDED
   │              │             │           └─────► ROLLED_BACK
   │              └─────► REJECTED
   ├────────────► APPROVED   (gate auto-approve path)
   └────────────► REJECTED
```

Terminal states: `REJECTED`, `ROLLED_BACK`, `SUPERSEDED`. Any other transition
raises `InvalidTransitionError`. Transitions never mutate — `with_status(...)`
returns a **new validated instance** (the old object is the audit record).

**Evidence invariant (enforced by the model):** a Proposal cannot be
constructed in, or transitioned to, `APPROVED` or `ACTIVE` with an empty
`evidence` tuple. Human sign-off is not an exemption: an operator approval is
recorded as an `EvidenceCitation(role=operator_decision)` — every promotion
cites, no exceptions.

## Evidence citations

`EvidenceCitation = {assertion_id, role, note}` where `role` ∈:

- `prior_outcome` — ledger outcomes for the same gate tuple (the Bayesian prior)
- `paired_replay` — SkillAudit-style with/without replay diff (Phase 1 task 1-6)
- `operator_decision` — recorded human approve/reject
- `constitution_check` — invariant check result attached at gate time
- `episode` — raw experience episodes cited by miners (migrates `derived_from`)
- `doc` — documentation citations (DocsMiner)

## Versioning and rollback

- `version` increments along the `supersedes` chain per (kind, target).
- Promoting v(n+1) transitions v(n) `ACTIVE → SUPERSEDED` (gate's job, Phase 1).
- Rollback ships as a **new** proposal with `rollback_of` set and payload equal
  to the prior version's — rollback is itself gated, cited, and audited.

## Ledger mapping (Phase 1)

Every gate verdict on a Proposal is written to mnema as an assertion whose
`derived_from` carries the citation ids; the Proposal `id` is the subject.
`why(promotion)` is then a ledger lookup. Until Phase 1 lands, `to_payload()`
gives the tracer-safe JSON rendering.

## Migration from legacy types

| Legacy | Disposition |
|---|---|
| `graxella.agenda.miners.Proposal` | **Deprecated** (marked in code). `kind`→`ArtifactKind`, `subject`→`target`, `change`→`payload`, `derived_from`→`evidence[role=episode]`, `evidence` (str)→`note`. Miners re-emit spec Proposals in Phase 1 task 1-5. |
| `graxella.gate.promoter.Proposal` + `GatePolicy` | **Interim, deprecated.** The scored `GatePolicy` is the explicitly rejected design (see gate design note); deleted in Phase 1 task 1-5. `blast_radius`/`status` map 1:1; `score`→`confidence`; `ObjectiveScores` is dropped, not migrated. |
| `Rulebook.promote(...)` | Becomes a gate-verdict consumer in Phase 1: rulebook writes require an ACTIVE spec Proposal. |

## Non-goals

- **No scoring weights in this spec.** Grading is the Evidence Gate's job and
  it is evidence-based, not weight-based.
- **No flow topology changes.** A proposal that would alter the user-defined
  workflow structure is invalid by construction — the constitution owns that
  boundary.

## Open questions (tracked for Phase 1)

1. Should `payload` be typed per-kind (discriminated union) instead of dict?
   Deferred: dict + per-kind informative shapes now, tighten when the kinds
   stabilize after Phase 2.
2. Cross-domain `transfer_from` seeding metadata — lands with Phase 5 task 5-3.
