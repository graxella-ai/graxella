# Changelog

All notable changes to `agent2society` are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project follows semantic versioning.

## [0.5.3] - 2026-06-25

Production-hardening release. No public-API additions beyond `SessionTracer`
and `TraceEvent` (the observability surface). Every other change is an
internal resilience fix surfaced by a v0.5.3 failure-mode audit.

### Added — observability

- **`SessionTracer`** and **`TraceEvent`** — a session-level join between
  the telemetry sink and the explanation store. One `SessionTracer.events()`
  call returns an ordered list of `TraceEvent`s (route → dispatch → return)
  with per-step latency, candidate ranking, and the rationale that drove
  each decision. The trace is built from already-recorded state — no new
  per-route cost.

### Changed — resilience hardening (internal)

All fifteen audit items are wired to a named test in
`tests/test_v053_resilience.py` so a regression breaks an obviously-named
case rather than getting buried in an integration test.

- **Dispatch invariant: never crash on AssertionError.** The post-loop
  `assert text is not None` in `Mesh.run` is replaced by an explicit
  `DispatchError` so an internal control-flow bug surfaces as a
  library-typed exception, not an opaque `AssertionError`.
- **Boundary mutation is copy-on-write.** `Society.boundary()` and
  `Society.depends_on()` now deep-copy the graph, apply edits, and swap
  the `(graph, router)` pair atomically — the same CoW pattern that
  `add()` and `apply_optimization()` already used. In-flight routes
  captured before the swap continue against a consistent snapshot.
- **Governance signature memory is bounded.** The `_fired_conflicts` /
  `_fired_drifts` deduplication sets are now ordered dicts with a
  default cap of 10 000 entries and FIFO eviction past the cap. Long-
  running processes can no longer leak memory through this path.
- **Router `_stale` rebuild is serialised.** `Router.route()` now uses
  double-checked locking around `_rebuild_index`, and the rebuild
  publishes the new `(_skill_index, _skill_vectors)` pair atomically.
  Concurrent first-time routes can no longer produce inconsistent index
  state. Each `route()` call captures local references so a concurrent
  `mark_stale()` cannot swap the index out mid-iteration.
- **`LocalTransport` wraps handler exceptions.** A user-supplied local
  handler that raises a plain `KeyError`, `TypeError`, etc. is now
  wrapped as `DispatchError` so the mesh's retry/fail path reacts as
  designed. Already-typed `DispatchError`s pass through unchanged
  (no double-wrapping).
- **`extract_text` is total.** Non-JSON-serialisable values in the
  response payload no longer crash the call; `extract_text` falls back
  to `json.dumps(..., default=str)` and finally `repr()`. The dispatch
  path treats this function as canonical, so it must never raise.
- **Adapter scan logs skipped adapters.** A noisy or buggy third-party
  adapter whose `matches()` raises is now logged at DEBUG and skipped,
  not silently dropped. Exceptions in registration scanning no longer
  vanish.
- **`JsonlFileStore` tracks corruption.** Construct-time line skips are
  counted and logged (`corruption_stats={"skipped_json": n, "skipped_shape": n}`).
  Operators can verify audit-log integrity without grepping the disk.
  Deserialization now also restores `margin` and `flags` so a v0.5.2
  audit log round-trips losslessly.
- **`as_mapping()` is a frozen snapshot.** Both `InMemoryStore` and
  `JsonlFileStore` now return a `_MappingView` built from a point-in-
  time deep snapshot. The optimizer backtest sees consistent state for
  the entire duration of its work — concurrent `put()` calls no longer
  shift the ground under it.
- **Conformance is unicode-bypass resistant.** Both task text and
  allow/deny terms are NFKC-normalised and casefolded before substring
  comparison, closing the visual-lookalike bypass (e.g. fullwidth Latin
  `ｒｅｆｕｎｄ` now matches deny term `refund`).
- **Governance hook exceptions are logged, not silenced.** The
  `_safe_call` helper that wraps every user-registered hook now logs a
  WARNING with the hook qualname and the exception class, so a buggy
  hook surfaces in production logs instead of vanishing. Dispatch is
  still never blocked by a hook.
- **`human_review_when` fails safe.** If the user-supplied predicate
  raises, the handoff is now escalated to human review by default
  (rather than silently passed through as `needs=False`). A new counter
  `agent2society_human_review_predicate_errors_total` increments on
  each failure.
- **Retry path attribution.** The dispatch retry loop now records every
  failed attempt (not just the last) as a fallback reason on the
  routing record, so explanations and telemetry reflect the full chain
  of attempts taken.
- **Snapshot reads of `_state` are explicit.** `Society.run()` now
  acquires `_swap_lock` for the one-pointer read at entry, making the
  CoW snapshot semantics explicit on every read path (was relying on
  GIL-atomic attribute reads before).
- **`CompositeTransport` uses a public `LocalTransport.has(url)` check**
  instead of reaching into the private `_handlers` dict — small but
  removes a cross-module private-attribute coupling.

### Added — internal logging hooks

- New `agent2society._logging` module attaches a `NullHandler` to the
  `agent2society` logger on import. Library code never calls
  `logging.basicConfig()` — production callers configure handlers and
  levels on the package logger as usual.

### Tests

- 16 new tests in `tests/test_v053_resilience.py` cover every item in the
  audit table above. Each test names the failure mode it pins.

Total suite: **112 green** (96 carried over from 0.5.2, plus 16 new).

## [0.5.2] - 2026-06-25

### Added — routing-quality signals

Three structured flags and a score-gap field now appear on every
`RoutingExplanation`, derived in O(1) from the already-sorted candidate
list — zero additional embedding calls or data structures.

- **`RoutingExplanation.margin`** — score gap between the top-1 and top-2
  candidate. `0.0` when fewer than two candidates exist. Available in
  `to_dict()` and `render()`.
- **`RoutingExplanation.flags`** — immutable `Tuple[str, ...]` containing
  zero or more of the following:
  - `"OOD"` — no candidate scored above `min_score` (out-of-domain task).
  - `"VECTOR_AMBIGUITY"` — the top-3 above-threshold candidates fall within
    a 0.05-point band of each other (winner is not statistically clear).
  - `"LOW_MARGIN"` — the score gap is narrower than the registered
    `low_margin_threshold`.
- **`Society.on_low_margin(handler, *, threshold=0.05)`** — governance hook
  fired before the transport call when `LOW_MARGIN` is set. Receives the
  `RoutingExplanation`; cannot block or mutate dispatch.
- **Three new Prometheus counters** pre-registered on every
  `MetricsCollector`:
  - `agent2society_low_margin_total`
  - `agent2society_ood_total`
  - `agent2society_vector_ambiguity_total`

### Implementation detail

All signal hooks fire **before** the transport attempt. Routing-quality
signals are properties of the routing *decision*, not the agent's
*response*, so they are recorded even when dispatch subsequently fails.
The module-level `_AMBIGUITY_BAND = 0.05` constant controls the
VECTOR_AMBIGUITY threshold and can be monkey-patched in tests.

### Tests

- 15 new tests in `tests/test_v052_signals.py`:
  - Pure-function unit tests of `_compute_routing_signals()` for all three
    flags, margin arithmetic, and edge cases (empty candidates, single
    candidate, `None` threshold).
  - Integration tests verifying `RoutingExplanation.margin` / `.flags` are
    populated, `to_dict()` serialises them, `render()` shows `margin=`, the
    `on_low_margin` hook fires and receives the correct explanation, and the
    three counters increment correctly.

Total suite: 96 green.

## [0.5.0] - 2026-06-25

### Added — production-ops surface

- **Thread-safe `Society`** — every mutation path (`add`, `boundary`,
  `depends_on`, `run`, `optimize`, `apply_optimization`) now acquires
  a reentrant lock. Concurrent `run()` calls from multiple threads no
  longer corrupt the explanation store or the routing index. The
  optimizer's in-place backtest swap is safe under contention because
  the lock is held for the duration.
- **`MetricsCollector`** — counters and summary-style histograms with
  thread-safe `inc()`/`observe()`. Available as `society.metrics`.
  - `metrics.snapshot()` returns a JSON-serialisable dict.
  - `metrics.render_prometheus()` emits the standard text exposition
    format (`# HELP` / `# TYPE` lines + samples). No new dependency —
    drop the output behind any scrape-pull stack (Prometheus,
    VictoriaMetrics, OpenMetrics).
  - Pre-registered series: `agent2society_routes_total`,
    `agent2society_dispatches_total`,
    `agent2society_dispatch_retries_total`,
    `agent2society_dispatch_failures_total`,
    `agent2society_conformance_blocked_total`,
    `agent2society_unroutable_total`,
    `agent2society_conflicts_detected_total`,
    `agent2society_low_confidence_total`,
    `agent2society_drift_detected_total`,
    `agent2society_human_review_total`,
    `agent2society_optimizer_edits_applied_total`,
    `agent2society_route_score`, `agent2society_request_tokens`,
    `agent2society_response_tokens`.
- **Pluggable `ExplanationStore`** — `Society(store=...)`. Default is
  the new `InMemoryStore` (same semantics as before); `JsonlFileStore`
  appends each explanation as one JSON line and rebuilds the index on
  construction, surviving process restarts. Corrupt lines are skipped
  rather than failing the load.
- **Auto-retry / fallback dispatch** — `society.run(handoff, retry=True)`
  walks the conformance-passing candidate list on `DispatchError` and
  dispatches to the next one. Each retry is recorded in the
  `RoutingRecord.fallbacks` chain and emits a
  `dispatch_retries_total` counter increment.
- **LLM-assisted optimizer mode** — optional `llm_fn=` on
  `society.optimize(labels, llm_fn=...)`. The LLM only *proposes*
  candidate tokens; suggestions go through the same filters
  (existing-text, generic-token, length, miss-density) and the same
  backtest as discriminative tokens, so the LLM never makes a routing
  decision. A crashing `llm_fn` falls back to observation-only mode.

### Tests

- 14 new tests in `tests/test_v05_production.py` covering thread
  safety under concurrent runs, RLock reentrance in `optimize()`,
  counter/histogram correctness, Prometheus output well-formedness,
  JSONL round-trip across societies, corrupt-line resilience,
  retry/no-retry paths and metrics, LLM generic-token filtering, and
  LLM crash isolation.

Total suite: 81 green.

## [0.4.0] - 2026-06-25

### Added — observation-driven routing improvement

- **`Society.optimize(labels)`** — mines discriminative tokens from
  labeled past decisions and proposes them as new skill tags. Pure
  observation + backtest; no LLM in the loop. Returns an
  `OptimizationReport`; nothing on the society changes until you call…
- **`Society.apply_optimization(report)`** — applies only the report's
  accepted (net-positive) edits. Two-step by design so every change is
  reviewable.
- **`OptimizationReport` / `SkillEdit`** — typed result objects exposed
  on the package root.
- **`benchmarks/run_optimization.py`** — end-to-end demonstration that
  closes the documented 9/11 → 11/11 gap on the default LangGraph
  scenarios without changing the embedder.
- **7 new tests** (`tests/test_optimizer.py`):
  - `optimize()` does not mutate the graph until `apply_optimization()`
  - the documented "correlations" miss is fixed and re-routes correctly
  - optimization is deterministic given the same graph + same labels
  - already-correct labels generate no edits
  - missing handoff ids are counted, not crash
  - rejected edits are never applied even when manually constructed
  - the report renders cleanly on a cp1252 console

### Mechanism, in one paragraph

For each labeled miss, the optimizer finds task tokens that (a) appear
in the missed task, (b) do not already appear in the correct skill's
text, (c) are not generic across the rest of the mesh. Each proposal is
backtested against the full label set: only edits whose `fixes -
regressions > 0` are accepted. The result on the bench: routing accuracy
on the labeled steps climbs from 81.8% to 100% on the same scorer.

## [0.3.0] - 2026-06-25

### Renamed

- Package renamed `lucent` → `agent2society` (PyPI). The primary public
  class is now `Society`. `Mesh` is kept as a back-compat alias so v0
  code (`from agent2society import Mesh` or earlier `from lucent import
  Mesh`) continues to import.
- `pyproject.toml` rewritten for PyPI publication: Beta classifier, AI
  topic, twelve keywords, expanded project URLs, `build` + `twine` in
  the `dev` extra.

### Public surface (carryover from v1.5)

- `Society` / `Mesh` facade with deterministic graph-routed dispatch.
- `Handoff` envelope (task + intent + assumptions + prior decisions +
  confidence threshold + human-review predicate).
- `SelfAssessment` surfaced on every routing explanation.
- `RoutingExplanation` with deterministic template rationale.
- Detection-only governance hooks: `on_conflict`,
  `on_low_confidence`, `on_capability_drift`, `on_human_review`.
- A2A JSON-RPC transport with `LocalTransport` + `HttpTransport`
  composite; adapters for CrewAI / LangGraph / AutoGen / plain
  callables.
- TF-IDF default scorer; `embed_fn=` for real embeddings.
- LangGraph head-to-head bench: 96% coordination-token reduction at
  ~200x cost ratio on the default scenarios.
