"""graxella.mastbench — MAST replay harness, Step 1 (task 2-8).

Ingests annotated multi-agent traces (JSONL) and replays them through
graxella's DETECTORS — no agents run, no LLM. The score answers one
question per failure mode: would graxella's shipped detectors have
flagged this labeled failure?

HONESTY CONTRACT (scorecard rule): these are *would-have-detected rates
on foreign traces* — never live prevention rates. Step 3 of the
scorecard's regression plan (fault-injection meshes) earns that claim.

Trace schema (one JSON object per line):
  {"trace_id": str, "failure_mode": "FM-1.3" | ... | null,
   "budget_hops": int (default 5),
   "events": [{"agent": str, "response": str,
               "tools_used": [str], "complete": bool}, ...]}

Run:  uv run python -m graxella.mastbench <traces.jsonl>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from graxella.instrument import _claimed_action


def detect_fm_1_3(trace: dict) -> bool:
    """Step repetition: a repeated (agent, response) signature."""
    seen: set[tuple] = set()
    for ev in trace.get("events", []):
        sig = (ev.get("agent"), hash((ev.get("response") or "")[:200]))
        if sig in seen:
            return True
        seen.add(sig)
    return False


def detect_fm_1_5(trace: dict) -> bool:
    """Termination-unawareness: chain exceeds budget, never completes."""
    events = trace.get("events", [])
    budget = int(trace.get("budget_hops") or 5)
    return len(events) > budget and not any(ev.get("complete")
                                            for ev in events)


def detect_fm_2_6(trace: dict) -> bool:
    """Reasoning–action mismatch: claimed action, empty tool trail."""
    return any(
        _claimed_action(ev.get("response") or "") and not ev.get("tools_used")
        for ev in trace.get("events", [])
    )


DETECTORS: dict[str, Callable[[dict], bool]] = {
    "FM-1.3": detect_fm_1_3,
    "FM-1.5": detect_fm_1_5,
    "FM-2.6": detect_fm_2_6,
}


def replay(traces: list[dict]) -> dict:
    """Per-mode would-have-detected rates + the false-positive rate on
    traces labeled clean (failure_mode null)."""
    stats = {m: {"labeled": 0, "detected": 0} for m in DETECTORS}
    clean_total = clean_flagged = 0
    for trace in traces:
        mode = trace.get("failure_mode")
        if mode in DETECTORS:
            stats[mode]["labeled"] += 1
            if DETECTORS[mode](trace):
                stats[mode]["detected"] += 1
        elif mode is None:
            clean_total += 1
            if any(fn(trace) for fn in DETECTORS.values()):
                clean_flagged += 1
    return {
        "modes": {
            m: {**v, "rate": round(v["detected"] / v["labeled"], 3)
                if v["labeled"] else None}
            for m, v in stats.items()
        },
        "clean": {"labeled": clean_total, "flagged": clean_flagged,
                  "false_positive_rate":
                      round(clean_flagged / clean_total, 3)
                      if clean_total else None},
        "disclaimer": "would-have-detected on foreign traces; "
                      "NOT live prevention rates",
    }


def load(path: str | Path) -> list[dict]:
    return [json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m graxella.mastbench <traces.jsonl>")
        return 2
    report = replay(load(args[0]))
    for mode, v in report["modes"].items():
        print(f"{mode}: {v['detected']}/{v['labeled']} detected "
              f"(rate={v['rate']})")
    c = report["clean"]
    print(f"clean traces flagged: {c['flagged']}/{c['labeled']} "
          f"(false-positive rate={c['false_positive_rate']})")
    print(f"NOTE: {report['disclaimer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
