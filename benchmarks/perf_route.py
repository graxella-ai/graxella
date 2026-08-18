"""Task 3-4 — the perf harness. Honest numbers first, CI gates second.

Measures, on this machine, with the ledger doing full evidence writes:
  * route() p50/p95 at N registered agents (the Phase 3 target: p50
    < 10ms @ 1k agents for routing; note that route() here INCLUDES
    decision+outcome ledger writes — the full governed dispatch)
  * EvidenceGate.prior() p50 over a seeded outcome ledger (target < 5ms)

Run:  uv run python benchmarks/perf_route.py [n_agents] [n_calls]
"""
from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

import graxella
from graxella.beliefs import Memory
from graxella.gate.evidence import EvidenceGate
from graxella.gate.spec import ArtifactKind, TargetScope


def make_agent(i: int):
    def agent(payload):
        return {"result": "ok"}
    agent.__name__ = f"agent_{i:04d}"
    agent.__doc__ = (f"handle domain {i % 20} tasks about topic{i} "
                     f"and variant {i % 7} operations")
    return agent


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(int(len(xs) * p), len(xs) - 1)]


def bench_route(n_agents: int, n_calls: int) -> None:
    work = Path(tempfile.mkdtemp(prefix="graxella-perf-"))
    memory = Memory.sqlite(str(work / "m.db"), agent_id="perf",
                           buffered=True)   # 3-1: the production default
    t0 = time.perf_counter()
    app = graxella.mesh([make_agent(i) for i in range(n_agents)],
                        memory=memory, store_path=str(work / "r.jsonl"),
                        recall=False)
    build_s = time.perf_counter() - t0

    lat = []
    for i in range(n_calls):
        t = time.perf_counter()
        app.route(f"handle domain {i % 20} tasks about topic{i % n_agents}")
        lat.append((time.perf_counter() - t) * 1000)
    print(f"route() @ {n_agents} agents (n={n_calls}, mesh build "
          f"{build_s:.1f}s):")
    print(f"  p50={pct(lat, 0.5):.1f}ms  p95={pct(lat, 0.95):.1f}ms  "
          f"(includes decision+outcome ledger writes)")


def bench_prior(n_outcomes: int = 2000) -> None:
    work = Path(tempfile.mkdtemp(prefix="graxella-perf-g-"))
    memory = Memory.sqlite(str(work / "m.db"), agent_id="perf")
    for i in range(n_outcomes):
        aid = memory.record_decision(decision_type="transform", task=f"t{i}",
                                     chosen="tool_x", domain=f"d{i % 10}")
        memory.record_outcome(decision_id=aid, ok=i % 5 != 0,
                              kind="transform", chosen="tool_x",
                              domain=f"d{i % 10}", session_id=f"s{i % 6}")
    gate = EvidenceGate(memory)
    gate.refresh()                       # index build (once per refresh)
    lat = []
    target = TargetScope(domain="d3", tool="tool_x")
    for _ in range(200):
        t = time.perf_counter()
        gate.prior(ArtifactKind.TRANSFORM, target)
        lat.append((time.perf_counter() - t) * 1000)
    print(f"gate.prior() over {n_outcomes} outcomes:")
    print(f"  p50={pct(lat, 0.5):.2f}ms  p95={pct(lat, 0.95):.2f}ms  "
          f"(post-refresh; refresh itself is the amortized cost)")


if __name__ == "__main__":
    n_agents = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    n_calls = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    bench_route(n_agents, n_calls)
    bench_prior()
