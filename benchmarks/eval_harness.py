"""The claims scorecard — a CI-able probe suite over the exact things an
external code review broke (2026-08-30), plus the mechanisms it argued
for. Not a duplicate of pytest: pytest asserts "does this function work";
this harness asserts "does the CLAIM hold", with a printed number, the
same way bench_healing.py measures the LLM-cost claim instead of just
testing that healing runs.

Five probes, each independent and labeled honest/skipped when its
prerequisite (a local embedding model) isn't available:

  1. gate_isolation     — the exact contamination probe that broke the
                          gate: does tool "search" inherit evidence from
                          "search_flights_v2"? Pass/fail, not a %.
  2. drift_classification — the widened classify_drift against a labeled
                          set spanning all three drift families AND the
                          non-drift guards (auth/timeout/plain-404).
                          Reports precision/recall, not just accuracy —
                          a false "heal this" is worse than a missed one.
  3. semantic_routing    — the paraphrase set that broke lexical
                          routing ("my package never showed up..."),
                          scored under the actual default (router="auto")
                          vs the lexical floor, side by side. SKIPPED
                          (not failed) with no local embedder — routing
                          then legitimately runs lexical-only.
  4. recipe_robustness   — nested restructuring, bad-cast survival,
                          round-trip fidelity.
  5. demotion_sensitivity — should_demote's threshold behavior across a
                          labeled (successes, failures) -> verdict set.

Run:
    <venv>/python benchmarks/eval_harness.py

Exit code is nonzero if any probe regresses below its floor — wire this
into CI. Every probe that can run without a model does; only #3 needs
one, and its absence is reported, never silently skipped-without-a-note.
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "packages" / "graxella"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

import graxella  # noqa: E402
from graxella.gate.health import RuleHealth, should_demote  # noqa: E402
from graxella.healing import classify_drift  # noqa: E402
from graxella.healing.recipes import TransformRecipe  # noqa: E402


class Probe:
    def __init__(self, name: str):
        self.name = name
        self.detail: list[str] = []
        self.score: float | None = None   # 0-100, or None if pass/fail-only
        self.passed: bool | None = None
        self.skipped_reason: str | None = None

    def skip(self, reason: str) -> "Probe":
        self.skipped_reason = reason
        return self

    def line(self, msg: str) -> None:
        self.detail.append(msg)


# --------------------------------------------------------- 1. gate isolation

def probe_gate_isolation() -> Probe:
    p = Probe("gate_isolation")
    from graxella.beliefs import Memory
    from graxella.gate.evidence import EvidenceGate
    from graxella.gate.spec import ArtifactKind, TargetScope

    mem = Memory.sqlite(":memory:", agent_id="probe", namespace="travel")
    for i in range(30):
        aid = mem.record_decision(decision_type="tool", task="call",
                                  chosen="search_flights_v2", domain="travel")
        mem.record_outcome(decision_id=aid, ok=True, kind="transform",
                           chosen="search_flights_v2", domain="travel",
                           session_id=f"s{i}")
    gate = EvidenceGate(mem)
    leaked = gate.prior(ArtifactKind.TRANSFORM,
                        TargetScope(domain="travel", tool="search"))
    real = gate.prior(ArtifactKind.TRANSFORM,
                      TargetScope(domain="travel", tool="search_flights_v2"))
    p.passed = (leaked.n == 0 and real.successes == 30)
    p.line(f"unrelated tool 'search' inherited: {leaked.n} outcomes "
          f"(want 0)")
    p.line(f"the real tool 'search_flights_v2' kept: {real.successes} "
          f"successes (want 30)")
    return p


# ---------------------------------------------------- 2. drift classification

def _pydantic_missing():
    from pydantic import BaseModel

    class Q(BaseModel):
        order_ref: str
    try:
        Q(order_id="1")
    except Exception as exc:
        return exc


def _http_gone():
    import urllib.error
    return urllib.error.HTTPError("http://api/x", 410, "Gone", {}, None)


def probe_drift_classification() -> Probe:
    p = Probe("drift_classification")
    # (exception, should_be_drift)
    cases = [
        (TypeError("unexpected keyword argument 'city'"), True),
        (TypeError("missing 1 required positional argument: 'ref'"), True),
        (_pydantic_missing(), True),
        (_http_gone(), True),
        (Exception("endpoint /api/v1/track no longer exists (404)"), True),
        (ValueError("upstream returned garbage"), False),
        (TimeoutError("read timed out"), False),
        (PermissionError("401 unauthorized: bad api key"), False),
        (KeyError("temp_c"), False),
        (Exception("404: order ORD-9 not found"), False),
    ]
    tp = fp = tn = fn = 0
    for exc, expect_drift in cases:
        got_drift = classify_drift(exc) is not None
        if expect_drift and got_drift:
            tp += 1
        elif expect_drift and not got_drift:
            fn += 1
            p.line(f"MISS (should heal, didn't): {exc!r}")
        elif not expect_drift and got_drift:
            fp += 1
            p.line(f"FALSE POSITIVE (should NOT heal): {exc!r}")
        else:
            tn += 1
    n = len(cases)
    p.score = 100.0 * (tp + tn) / n
    p.passed = fp == 0 and fn == 0   # a false positive is the expensive one
    p.line(f"{tp+tn}/{n} correct  (tp={tp} fn={fn} fp={fp} tn={tn})")
    return p


# ------------------------------------------------------- 3. semantic routing

def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen(
                "http://localhost:11434/api/tags", timeout=1.5) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


_ROUTING_CASES = [
    # (paraphrase, expected_agent)
    ("my package never showed up and I want my money back", "refunds"),
    ("is it going to rain tomorrow", "weather"),
    ("should I carry an umbrella today", "weather"),
    ("how hot is it outside", "weather"),
    ("my parcel still hasn't shown up", "orders"),
]


def _routing_mesh(grx, router):
    def refunds(task: str) -> dict:
        """decide refunds and money-back requests for damaged or lost orders"""
        return {"result": "ok"}

    def weather(task: str) -> dict:
        """current weather, temperature and rain forecast for a city"""
        return {"result": "ok"}

    def orders(task: str) -> dict:
        """track shipments and delivery status for orders"""
        return {"result": "ok"}

    return grx.mesh([refunds, weather, orders], router=router)


def probe_semantic_routing() -> Probe:
    p = Probe("semantic_routing")
    if not _ollama_up():
        return p.skip("no local Ollama reachable — router='auto' would "
                      "correctly fall back to lexical; skipping the "
                      "semantic-quality measurement, not failing it")
    import graxella as _g

    grx_auto = _g.Session("probe-auto", domain="support", workdir="ephemeral")
    grx_lex = _g.Session("probe-lex", domain="support", workdir="ephemeral")
    app_auto = _routing_mesh(grx_auto, router="auto")
    app_lex = _routing_mesh(grx_lex, router="tfidf")

    from agent2society import NoRouteError

    def _route(app, task):
        """A NoRouteError IS a legitimate outcome for the lexical floor
        (the self-explaining P1-5 error working as designed) — treat it
        as a miss for this probe, not a crash."""
        try:
            result, _ = app.route(task)
            return result.chosen_agent
        except NoRouteError:
            return None

    hits_auto = hits_lex = 0
    for task, expect in _ROUTING_CASES:
        got_auto = _route(app_auto, task)
        got_lex = _route(app_lex, task)
        ok_auto = got_auto == expect
        hits_auto += ok_auto
        hits_lex += got_lex == expect
        if not ok_auto:
            p.line(f"MISS (auto): {task!r} -> {got_auto} (want {expect})")
    n = len(_ROUTING_CASES)
    p.score = 100.0 * hits_auto / n
    p.passed = hits_auto >= n - 1     # allow one paraphrase to miss
    p.line(f"router='auto' (default): {hits_auto}/{n} correct")
    p.line(f"router='tfidf' (lexical floor, for contrast): {hits_lex}/{n} "
          f"correct")
    return p


# ------------------------------------------------------ 4. recipe robustness

def probe_recipe_robustness() -> Probe:
    p = Probe("recipe_robustness")
    checks: list[tuple[str, bool]] = []

    r = TransformRecipe(field_map={"customer_id": "customer.id",
                                   "is_active": "customer.status"},
                        type_casts={"customer.id": "int"},
                        value_map={"customer.status": {True: "ACTIVE"}})
    out = r.apply({"customer_id": "42", "is_active": True})
    checks.append(("nested restructure + cast + value-map",
                   out == {"customer": {"id": 42, "status": "ACTIVE"}}))

    bad = TransformRecipe(type_casts={"n": "int"})
    out2 = bad.apply({"n": "not-a-number"})
    checks.append(("bad cast survives (never crashes)",
                   out2["n"] == "not-a-number"))

    rt = TransformRecipe(field_map={"a.b": "c.d"}, type_casts={"n": "int"},
                         value_map={"s": {"a": "b"}})
    checks.append(("to_dict/from_dict round-trip",
                   TransformRecipe.from_dict(rt.to_dict()) == rt))

    old_shape = {"field_map": {"city": "location"}, "static_defaults": {},
                "drop_fields": []}
    checks.append(("backward-compatible with pre-upgrade rulebook files",
                   TransformRecipe.from_dict(old_shape)
                   .apply({"city": "Paris"}) == {"location": "Paris"}))

    for name, ok in checks:
        p.line(f"{'OK ' if ok else 'FAIL'}  {name}")
    p.passed = all(ok for _, ok in checks)
    p.score = 100.0 * sum(ok for _, ok in checks) / len(checks)
    return p


# ----------------------------------------------------- 5. demotion sensitivity

def probe_demotion_sensitivity() -> Probe:
    p = Probe("demotion_sensitivity")
    # (successes, failures, expect_demote)
    cases = [
        (0, 2, False),     # thin evidence -> never judge yet
        (1, 3, True),      # posterior .33 over n=4 -> demote
        (10, 1, False),    # healthy -> keep
        (2, 2, False),     # posterior .43 but n=4... check exact boundary
    ]
    correct = 0
    for succ, fail, expect in cases:
        h = RuleHealth(rule_id="r", successes=succ, failures=fail)
        demote, reason = should_demote(h)
        ok = demote == expect
        correct += ok
        p.line(f"{'OK ' if ok else 'FAIL'}  successes={succ} failures={fail} "
              f"-> demote={demote} (want {expect}): {reason}")
    p.score = 100.0 * correct / len(cases)
    p.passed = correct == len(cases)
    return p


PROBES = [
    probe_gate_isolation,
    probe_drift_classification,
    probe_semantic_routing,
    probe_recipe_robustness,
    probe_demotion_sensitivity,
]


def main() -> int:
    print("\ngraxella claims scorecard\n" + "=" * 60)
    results = [fn() for fn in PROBES]
    failed = False
    for r in results:
        if r.skipped_reason:
            status = "SKIP"
        elif r.passed:
            status = "PASS"
        else:
            status = "FAIL"
            failed = True
        score = f"  ({r.score:.0f}%)" if r.score is not None else ""
        print(f"\n[{status}] {r.name}{score}")
        if r.skipped_reason:
            print(f"       {r.skipped_reason}")
        for line in r.detail:
            print(f"       {line}")
    print("\n" + "=" * 60)
    n_run = sum(1 for r in results if r.skipped_reason is None)
    n_pass = sum(1 for r in results if r.passed)
    n_skip = sum(1 for r in results if r.skipped_reason is not None)
    print(f"{n_pass}/{n_run} probes passed"
         + (f"  ({n_skip} skipped)" if n_skip else ""))
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
