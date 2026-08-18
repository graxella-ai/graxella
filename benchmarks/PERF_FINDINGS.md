# Perf findings (task 3-4 — 2026-08-18, dev laptop)

## After the routing fix (sparse cosine + precomputed aux + route memo)

| Metric | Target | Before | After |
|---|---|---|---|
| route() p50 @ 1000 agents | < 10 ms (routing) | 1174 ms | **33.5 ms** (35×; includes 2 ledger writes ≈ 10 ms — remaining gap closes with 3-1's write buffer) |
| route() p95 @ 1000 agents | — | 1210 ms | 45.1 ms |
| mesh build @ 1000 agents | — | 38 s | **0.1 s** (batch add_many: ONE copy-on-write cycle instead of a deepcopy per agent — the per-add deepcopy was O(n^2)) |

Fixes: `sparse_cosine` over nonzero terms (O(query terms) per skill,
was O(vocab)); skill tokens/tags precomputed at rebuild (was re-tokenized
per call); size-1 route memo collapses the L1-pre-route + dispatch-route
pair into one scoring pass. agent2society suite green throughout.

## Baseline (before fixes)

Measured with `benchmarks/perf_route.py` (full governed dispatch:
routing + dispatch + decision/outcome ledger writes):

| Metric | Target (Phase 3) | Measured | Verdict |
|---|---|---|---|
| gate.prior() p50, 2k outcomes | < 5 ms | **0.04 ms** | ✅ crushed |
| route() p50 @ 200 agents | — | 64 ms | ⚠ ledger writes + double routing |
| route() p50 @ 1000 agents | < 10 ms (routing) | **1174 ms** | ❌ 100× over, superlinear |
| mesh build @ 1000 agents | — | 38 s | ❌ needs index build profiling |

## Analysis (to verify by profiling — next session's first task)

1. **Double routing per dispatch**: `Society.route()` pre-routes for L1
   disclosure (task 2-3) AND `Mesh.run()` routes again — two full TF-IDF
   passes per call. Fix: reuse one candidate list for both.
2. **Pure-Python TF-IDF cosine** over the full skill corpus per query in
   agent2society's embedder/router — no vectorization, no query cache.
   Superlinear growth suggests per-call refit or O(n·vocab) scans.
3. **Synchronous SQLite writes** (decision + outcome per dispatch) are
   the floor at small n — the Phase 3 data-plane buffer (task 3-1) is
   the designed fix.

## Rule

Perf targets become CI assertions only after the fixes land — a red
number in this file is honest; a green fake in CI is not.
