# ADR-0002: Consolidation Artifacts — `Rule`, `Skill`, `Digest` (Sprint 2)

Date: 2026-07-04 · Status: Accepted · Depends on: ADR-0001

## Decision
Introduce three new frozen domain models in `mnema/core/consolidation.py`:
`Rule`, `Skill`, `Digest`. They are the outputs of the sleep-phase
consolidator (Sprint 2, S2.2) and the substrate for the "consolidated" arm
of experiment E1. They mirror `Assertion`'s discipline:

- **I1** Immutable (`ConfigDict(frozen=True)`). Revision = new instance +
  `superseded_by` link on the old one (set atomically during the next
  digest's save).
- **I3** Content-addressed: `content_hash` covers the propositional content
  only (Rule: `text` + `scope` + `derived_from`; Skill: `code` + `signature`
  + `derived_from`). Never covers counts, supersession, or timestamps.
- **I4** Provenance-mandatory: `derived_from` is a non-empty tuple of
  Assertion ids. There is no anonymous rule and no anonymous skill.
- **I5** `core/consolidation.py` imports pydantic + stdlib only. `ast` for
  skill-code validation is stdlib; no third-party parser.

## Storage shape
- Rules and skills live in their own tables in the SQLite adapter, each
  keyed by an id and carrying a `digest_version` (which digest introduced
  them) and a nullable `superseded_by`.
- Digests are a manifest table (`version`, `namespace`, `agent_id`,
  `source_event_seq_range`, `created_at`). Loading a `Digest` from the repo
  hydrates it with the rules and skills that are **active as of that
  version** (created at or before `version`, not superseded by anything at
  or before `version`).
- Saving a digest emits exactly ONE `CONSOLIDATION_RUN` event **in the
  same transaction** as the row inserts/updates (mirrors `record()` for
  assertions).

## Skill outcomes are NOT WAL-tracked (deliberate)
`Skill.success_count` and `Skill.failure_count` are internal metrics, not
beliefs. `mark_skill_outcome()` is an atomic UPDATE on the skill row; no
new event type. The reserved event catalog (ADR-0001) is unchanged.

## `Digest.render()`
Deterministic markdown of active rules (by confidence desc, then created_at
asc) and skill signatures (by signature asc). Bounded by `max_chars`
(default 4000). Overflow drops **entire lowest-confidence rules last-first**;
skill signatures are dropped only after all rules are gone. Never truncates
mid-rule. Same digest → byte-identical string across processes.

## Consequences
- E1's A2 arm consumes `latest_digest().render(4000)` at every task —
  bounded prompt injection is a first-class concern of the schema.
- Time-travel (`get_digest(v)`) gives the "what did the agent know at
  consolidation N?" primitive for later differential evaluation.
- No changes to `models.py` or `events.py`. Sprint 0's freeze holds.

## Deliberate exclusions (out of scope for v0.0-consolidation)
- Embeddings on rules/skills (adapter concern; deferred to A1′ sprint).
- Cross-namespace rule sharing / transactive memory (later sprint).
- Skill versioning beyond `superseded_by` chain (fine-grained deltas can
  be reconstructed from consolidation-run event payloads if ever needed).
