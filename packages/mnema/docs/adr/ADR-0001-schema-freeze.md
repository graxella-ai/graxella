# ADR-0001: Assertion Schema Freeze (v0.0)

Date: 2026-07-04 · Status: Accepted

## Decision
The `Assertion` model in `mnema/core/models.py` is frozen at schema_version "0.0".
Changes require a new ADR and a schema_version bump. The property tests in
`tests/test_assertion_schema.py` are the executable specification.

## Invariants
- I1 Immutability + supersession chain (belief revision, never UPDATE)
- I2 Bi-temporality: valid_from/valid_to (event time) vs asserted_at (transaction time)
- I3 Content-addressed identity: hash over propositional content only
- I4 Mandatory provenance with derivation links (retraction cascade substrate)
- I5 Core imports pydantic + stdlib only (hexagonal boundary)

## Deliberate exclusions (and why)
- **Embeddings are NOT in the schema.** They are adapter artifacts, versioned by
  embedder model_id, keyed by content_hash. Re-embedding after a model upgrade is
  an index rebuild, never a schema migration.
- **No graph structure in the schema.** SPO fields are optional hooks; the graph
  is a projection built from assertions, not the store of record.
- **Confidence is heuristic in v0.** The field is frozen; its *semantics*
  (calibration method) are versioned via Confidence.method.

## Consequences
- The WAL (events table) is the source of truth; assertions are a projection.
- `current_beliefs(as_of=...)` gives transaction-time travel from day one — this
  is the primitive LTCF-Bench's differential evaluation is built on.
- Multi-agent namespacing (namespace + agent_id) costs nothing now, saves a
  migration when fleet-level experiments start.
