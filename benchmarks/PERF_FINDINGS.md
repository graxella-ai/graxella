# Perf findings (task 3-4 baseline — 2026-08-18, dev laptop)

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
