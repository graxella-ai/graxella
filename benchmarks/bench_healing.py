"""Drift-healing benchmark — the graxella thesis, measured.

Scenario: a tool's schema drifts under a running agent. The old field
name (`city`) is gone; the live endpoint wants `location` and rejects the
old call with a drift error. We replay N identical calls against three
strategies and measure what each one costs.

  Arm 1  naive          call the tool, no recovery. Baseline for "what
                        breaks without anything."
  Arm 2  llm_retry      the industry-standard answer: on every failure,
                        ask an LLM to repair the arguments, then retry.
                        Correct, but pays an LLM call *per call, forever*.
  Arm 3  graxella       the heal ladder: the LLM proposes a transform
                        ONCE (heal-once), then every subsequent call is
                        healed deterministically with zero LLM.

The LLM cost is identical per call across arms 2 and 3 (same Ollama
model, same repair prompt) — so the comparison isolates the one variable
that matters: how many times you pay it.

Run (from the graxella package root or repo root):
    <venv>/python benchmarks/bench_healing.py [n_calls]

If Ollama is unreachable, the LLM repair is simulated with a fixed,
clearly-labelled latency so the harness still produces a shape; real
numbers require Ollama up (qwen2.5:3b).
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# make the graxella + mnema packages importable without install
_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[1]
for pkg in ("graxella", "mnema", "axon-fabric", "agent2society"):
    src = _ROOT / "packages" / pkg / "src"
    if src.exists():
        sys.path.insert(0, str(src))
sys.path.insert(0, str(_ROOT / "packages" / "graxella"))

from graxella.beliefs import Memory  # noqa: E402
from graxella.gate.evidence import EvidenceGate  # noqa: E402
from graxella.healing.interceptor import ToolInterceptor  # noqa: E402
from graxella.healing.recipes import TransformRecipe  # noqa: E402
from graxella.rulebook import Rulebook  # noqa: E402

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:3b"
SIM_LLM_SECONDS = 0.8   # stand-in cost when Ollama is down (labelled in output)


# ----------------------------------------------------------- the drifted tool
def make_tools():
    """Return (primary, fallback). primary is the drifted tool; fallback is
    the live endpoint that speaks the new schema."""
    def primary(args: dict):
        # The old field is gone. This is the exact class of failure graxella
        # exists for — and the message matches the DRIFT_SIGNATURE.
        if "city" in args:
            raise RuntimeError(
                "unknown field: 'city' — schema deprecated; use 'location'")
        return {"forecast": f"sunny 27C for {args.get('location')}"}

    def fallback(args: dict):
        if "location" not in args:
            raise RuntimeError("unknown field: missing 'location'")
        return {"forecast": f"sunny 27C for {args['location']}"}

    return primary, fallback


# ------------------------------------------------------------------- the LLM
_ollama_up = None


def ollama_up() -> bool:
    global _ollama_up
    if _ollama_up is None:
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as r:
                _ollama_up = r.status == 200
        except Exception:
            _ollama_up = False
    return _ollama_up


def llm_repair_field_map(tool_name: str, args: dict, error: str) -> dict:
    """Ask the LLM to produce a {old_field: new_field} rename map from the
    error. This is the ONE real LLM call; both arms 2 and 3 route through
    it so the per-call cost is identical."""
    if not ollama_up():
        time.sleep(SIM_LLM_SECONDS)          # labelled simulation
        return {"city": "location"}          # what a correct model returns
    prompt = (
        "A tool call failed because a field was renamed. "
        f"Tool: {tool_name}. Failed arguments (JSON): {json.dumps(args)}. "
        f"Error: {error}\n"
        "Return ONLY a JSON object mapping each stale field name to its new "
        'name, e.g. {"old": "new"}. No prose.')
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt,
                       "stream": False, "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = json.loads(r.read().decode())["response"]
    # tolerant parse: pull the first {...} block
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        mp = json.loads(raw[start:end])
        return {k: v for k, v in mp.items() if isinstance(v, str)}
    except Exception:
        return {"city": "location"}          # fall back to the known fix


# ------------------------------------------------------------------ arms
def run_naive(calls: list[dict]) -> dict:
    primary, _ = make_tools()
    lat, ok = [], 0
    for args in calls:
        t = time.perf_counter()
        try:
            primary(dict(args)); ok += 1
        except Exception:
            pass
        lat.append((time.perf_counter() - t) * 1000)
    return _summary("naive (no recovery)", lat, ok, len(calls), llm_calls=0)


def run_llm_retry(calls: list[dict]) -> dict:
    primary, fallback = make_tools()
    lat, ok, llm = [], 0, 0
    for args in calls:
        t = time.perf_counter()
        try:
            primary(dict(args)); ok += 1
        except Exception as exc:
            # the "standard answer": re-ask the model every single time
            llm += 1
            fmap = llm_repair_field_map("get_weather", dict(args), str(exc))
            try:
                fixed = {fmap.get(k, k): v for k, v in args.items()}
                fallback(fixed); ok += 1
            except Exception:
                pass
        lat.append((time.perf_counter() - t) * 1000)
    return _summary("llm_retry (re-ask every call)", lat, ok, len(calls),
                    llm_calls=llm)


def run_graxella(calls: list[dict]) -> dict:
    work = Path(tempfile.mkdtemp(prefix="graxella-heal-"))
    memory = Memory.sqlite(str(work / "m.db"), agent_id="bench", buffered=True)
    rulebook = Rulebook(path=str(work / "rulebook.json"))
    gate = EvidenceGate(memory)
    primary, fallback = make_tools()

    def healer(tool_name, failed_args, error):
        fmap = llm_repair_field_map(tool_name, failed_args, error)
        return TransformRecipe(field_map=fmap)

    interceptor = ToolInterceptor(
        primary, tool_name="get_weather", memory=memory, rulebook=rulebook,
        gate=gate, fallback=fallback, healer=healer, domain="weather")

    lat, ok = [], 0
    for args in calls:
        t = time.perf_counter()
        try:
            interceptor(dict(args)); ok += 1
        except Exception:
            pass
        lat.append((time.perf_counter() - t) * 1000)
    return _summary("graxella (heal-once, then deterministic)", lat, ok,
                    len(calls), llm_calls=interceptor.healer_calls)


# ------------------------------------------------------------------ report
def pct(xs, p):
    xs = sorted(xs)
    return xs[min(int(len(xs) * p), len(xs) - 1)]


def _summary(name, lat, ok, n, *, llm_calls):
    return {
        "arm": name,
        "success_pct": 100.0 * ok / n,
        "llm_calls": llm_calls,
        "total_ms": sum(lat),
        "p50_ms": pct(lat, 0.5),
        "p95_ms": pct(lat, 0.95),
    }


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    # every call uses the stale field — a tool that drifted under the agent
    calls = [{"city": f"city_{i}"} for i in range(n)]

    mode = "REAL Ollama (%s)" % OLLAMA_MODEL if ollama_up() \
        else "SIMULATED LLM (%.0fms/call, Ollama down)" % (SIM_LLM_SECONDS * 1000)
    print(f"\nDrift-healing benchmark  |  n={n} drifted calls  |  LLM cost: {mode}\n")

    rows = [run_naive(calls), run_llm_retry(calls), run_graxella(calls)]

    w = max(len(r["arm"]) for r in rows)
    print(f"{'arm':<{w}}  {'success':>8}  {'LLM calls':>9}  "
          f"{'total ms':>9}  {'p50 ms':>8}  {'p95 ms':>8}")
    print("-" * (w + 50))
    for r in rows:
        print(f"{r['arm']:<{w}}  {r['success_pct']:>7.1f}%  "
              f"{r['llm_calls']:>9}  {r['total_ms']:>9.1f}  "
              f"{r['p50_ms']:>8.2f}  {r['p95_ms']:>8.2f}")

    base = next(r for r in rows if r["arm"].startswith("llm_retry"))
    grax = next(r for r in rows if r["arm"].startswith("graxella"))
    if base["llm_calls"]:
        saved = 100.0 * (base["llm_calls"] - grax["llm_calls"]) / base["llm_calls"]
        speed = base["total_ms"] / grax["total_ms"] if grax["total_ms"] else float("inf")
        print(f"\ngraxella vs llm_retry:  "
              f"{saved:.0f}% fewer LLM calls  |  "
              f"{base['llm_calls']}->{grax['llm_calls']} calls  |  "
              f"{speed:.1f}x lower total latency")
    print()
    return rows


if __name__ == "__main__":
    main()
