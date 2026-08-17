"""Runtime guardrails for MCP toolpoints.

Three layers, all pure-Python, all optional, all inspectable in the
ExperienceStore after the fact:

  CircuitBreaker      per-tool health tracking; after N consecutive
                      failures the circuit trips OPEN and further calls
                      short-circuit without hitting the endpoint. After
                      a cooldown the circuit transitions to HALF-OPEN
                      and admits one probe call.

  InvocationBudget    per-request cap on how many times a single tool
                      may be invoked. Stops a ReAct loop from calling
                      the same broken tool 20 times in one turn.

  guarded_wrap()      returns a new list of LangChain tools with the
                      breaker + budget + timeout + substitute-dispatch
                      baked into each tool's `.invoke`. Drop-in
                      replacement for the raw tool list.

Substitutes are looked up in a Graxella `Rulebook` (via
`find_substitution`). If a broken tool has a promoted successor, calls
that arrive AFTER the circuit trips are dispatched to the successor
instead of failing — same UX as if the primary had worked.

Design principle: fail fast, fail visible, fail cheap. Every guarded
call emits a structured `GuardEvent` so the caller can log or aggregate
them; the guardrails themselves never print or raise silently.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator

from langchain_core.tools import BaseTool

from graxella.healing.recipes import TransformRecipe
from graxella.rulebook import Rulebook


# ---------------------------------------------------------------- events
class GuardOutcome(str, Enum):
    OK = "ok"
    FAIL = "fail"
    CIRCUIT_OPEN = "circuit_open"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TIMEOUT = "timeout"
    SUBSTITUTED = "substituted"


@dataclass
class GuardEvent:
    tool: str
    outcome: GuardOutcome
    wall_s: float
    substituted_with: str | None = None
    err: str | None = None


# ---------------------------------------------------------------- circuit
class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-tool failure-counting breaker with cooldown + half-open probe.

    Not thread-hardened for high concurrency — a demo-grade implementation
    that captures the state-machine faithfully. For a production breaker
    swap in resilience4j / py-breaker after the pattern proves out.
    """
    failure_threshold: int = 2
    cooldown_seconds: float = 30.0

    _state: dict[str, CircuitState] = field(default_factory=dict)
    _failures: dict[str, int] = field(default_factory=dict)
    _opened_at: dict[str, float] = field(default_factory=dict)

    def state(self, name: str) -> CircuitState:
        st = self._state.get(name, CircuitState.CLOSED)
        if st is CircuitState.OPEN:
            if time.time() - self._opened_at.get(name, 0.0) > self.cooldown_seconds:
                self._state[name] = CircuitState.HALF_OPEN
                return CircuitState.HALF_OPEN
        return st

    def allow(self, name: str) -> bool:
        return self.state(name) is not CircuitState.OPEN

    def record_success(self, name: str) -> None:
        self._failures[name] = 0
        self._state[name] = CircuitState.CLOSED

    def record_failure(self, name: str) -> None:
        self._failures[name] = self._failures.get(name, 0) + 1
        if self._failures[name] >= self.failure_threshold:
            self._state[name] = CircuitState.OPEN
            self._opened_at[name] = time.time()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Human-readable state dump for the metrics table."""
        return {
            name: {
                "state": self.state(name).value,
                "failures": self._failures.get(name, 0),
            }
            for name in set(self._failures) | set(self._state)
        }


# ---------------------------------------------------------------- budget
@dataclass
class InvocationBudget:
    """Per-request cap on invocations of the same tool.

    Reset() at the start of every request. Between resets, `check(name)`
    tells the wrapper whether another call is allowed; `bump(name)` is
    called on every attempted invocation (successful or not) so a broken
    tool can't burn the budget silently.
    """
    max_calls_per_tool: int = 2
    _counts: dict[str, int] = field(default_factory=dict)

    def check(self, name: str) -> bool:
        return self._counts.get(name, 0) < self.max_calls_per_tool

    def bump(self, name: str) -> None:
        self._counts[name] = self._counts.get(name, 0) + 1

    def reset(self) -> None:
        self._counts.clear()

    def snapshot(self) -> dict[str, int]:
        return dict(self._counts)


# ---------------------------------------------------------------- timeout
def _run_with_timeout(fn: Callable[[], Any], timeout_s: float) -> Any:
    """Run `fn` in a daemon thread; raise TimeoutError if it exceeds timeout_s.

    Thread-based (not signal-based) so it works on Windows and inside
    subthreads. If the wrapped call is stuck in a C extension the thread
    will leak — the trade-off is portability, and for a demo aborting the
    caller-visible wait is what matters.
    """
    result: list[Any] = []
    error: list[BaseException] = []

    def _target() -> None:
        try:
            result.append(fn())
        except BaseException as e:  # noqa: BLE001 — deliberate
            error.append(e)

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"call exceeded {timeout_s}s")
    if error:
        raise error[0]
    return result[0]


# ---------------------------------------------------------------- wrap
def _recipe(rule_recipe: dict[str, Any]) -> TransformRecipe:
    return TransformRecipe(
        field_map=dict(rule_recipe.get("field_map", {})),
        static_defaults=dict(rule_recipe.get("static_defaults", {})),
        drop_fields=tuple(rule_recipe.get("drop_fields", ())),
    )


@contextmanager
def guarded_wrap(tools: list[BaseTool], *,
                 breaker: CircuitBreaker,
                 budget: InvocationBudget,
                 rulebook: Rulebook | None = None,
                 intent: str | None = None,
                 timeout_s: float = 3.0,
                 events: list[GuardEvent] | None = None,
                 substitute_pool: dict[str, BaseTool] | None = None
                 ) -> Iterator[list[BaseTool]]:
    """Context manager. Patches each tool's `.invoke` with a guarded version
    on __enter__, restores originals on __exit__ — no state leaks across
    calls, no shared mutable global side effects.

    Usage:
        with guarded_wrap(tools, breaker=..., budget=..., ...) as guarded:
            trace = react_run(llm_factory, guarded, query)

    `substitute_pool` lets us dispatch to a tool that isn't in the bound
    set — the raison d'etre of a rulebook substitution when the primary is
    down. If omitted we fall back to searching within `tools`.
    """
    by_name = {t.name: t for t in tools}
    pool = substitute_pool or by_name
    events = events if events is not None else []

    def _wrap(tool: BaseTool) -> BaseTool:
        original_invoke = type(tool).invoke.__get__(tool, type(tool))
        name = tool.name

        def _routed(input_: Any, config: Any = None) -> Any:
            t0 = time.time()

            # Budget check — hard stop on same-tool retry storms.
            if not budget.check(name):
                ev = GuardEvent(tool=name,
                                outcome=GuardOutcome.BUDGET_EXHAUSTED,
                                wall_s=round(time.time() - t0, 3),
                                err=f"budget cap {budget.max_calls_per_tool} reached")
                events.append(ev)
                return f"[guardrail] {name}: request budget exhausted; skipping."
            budget.bump(name)

            is_tool_call = (isinstance(input_, dict)
                            and input_.get("type") == "tool_call"
                            and isinstance(input_.get("args"), dict))
            args = input_["args"] if is_tool_call else input_

            # Proactive substitute dispatch: if the rulebook has a promoted
            # rule for this tool, dispatch to the successor directly and
            # never touch the primary. This matches graxella.wrap() semantics
            # — an operator who has approved the rule already knows the
            # primary is broken; calling it just wastes latency and logs.
            sub = _find_substitute(name, args, rulebook, intent, pool)
            if sub is not None:
                sub_name, sub_args, sub_tool = sub
                try:
                    val = _run_with_timeout(
                        lambda: sub_tool.invoke(sub_args, config=config),
                        timeout_s,
                    )
                except BaseException as e:  # noqa: BLE001
                    ev = GuardEvent(tool=name,
                                    outcome=GuardOutcome.FAIL,
                                    wall_s=round(time.time() - t0, 3),
                                    substituted_with=sub_name,
                                    err=f"substitute {sub_name} failed: {e}")
                    events.append(ev)
                    return f"[guardrail] {name}: substitute {sub_name} failed ({e})."
                ev = GuardEvent(tool=name,
                                outcome=GuardOutcome.SUBSTITUTED,
                                wall_s=round(time.time() - t0, 3),
                                substituted_with=sub_name)
                events.append(ev)
                return val

            # No substitute available. Enforce the circuit breaker — if the
            # tool has tripped, refuse the call immediately.
            if not breaker.allow(name):
                ev = GuardEvent(tool=name,
                                outcome=GuardOutcome.CIRCUIT_OPEN,
                                wall_s=round(time.time() - t0, 3),
                                err="circuit open, no substitute available")
                events.append(ev)
                return f"[guardrail] {name}: circuit open (too many failures); no substitute available. Try a different tool."

            # Real call with timeout.
            try:
                val = _run_with_timeout(
                    lambda: original_invoke(input_, config=config),
                    timeout_s,
                )
            except TimeoutError as e:
                breaker.record_failure(name)
                ev = GuardEvent(tool=name,
                                outcome=GuardOutcome.TIMEOUT,
                                wall_s=round(time.time() - t0, 3),
                                err=str(e))
                events.append(ev)
                return f"[guardrail] {name}: timed out after {timeout_s}s. Try a different tool."
            except BaseException as e:  # noqa: BLE001
                breaker.record_failure(name)
                ev = GuardEvent(tool=name,
                                outcome=GuardOutcome.FAIL,
                                wall_s=round(time.time() - t0, 3),
                                err=f"{type(e).__name__}: {e}")
                events.append(ev)
                return f"[guardrail] {name}: {type(e).__name__}: {e}. Try a different tool."

            breaker.record_success(name)
            ev = GuardEvent(tool=name,
                            outcome=GuardOutcome.OK,
                            wall_s=round(time.time() - t0, 3))
            events.append(ev)
            return val

        object.__setattr__(tool, "invoke", _routed)
        return tool

    patched: list[BaseTool] = []
    try:
        patched = [_wrap(t) for t in tools]
        yield patched
    finally:
        for t in patched:
            try:
                object.__delattr__(t, "invoke")
            except AttributeError:
                pass


def _find_substitute(name: str, args: dict[str, Any],
                     rulebook: Rulebook | None,
                     intent: str | None,
                     pool: dict[str, BaseTool]
                     ) -> tuple[str, dict[str, Any], BaseTool] | None:
    if rulebook is None:
        return None
    rule = rulebook.find_substitution(name, intent=intent)
    if rule is None and intent is not None:
        rule = rulebook.find_substitution(name, intent=None)
    if rule is None:
        return None
    sub_tool = pool.get(rule.with_skill)
    if sub_tool is None:
        return None
    translated = _recipe(rule.recipe).apply(args)
    return rule.with_skill, translated, sub_tool
