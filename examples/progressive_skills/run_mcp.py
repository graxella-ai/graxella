"""Side-by-side: unguarded MCP tools vs Graxella-guarded MCP tools.

Four scenarios, all against the same qwen2.5:3b via a real ReAct loop:

  A. Endpoint DOWN         -> substitute must dispatch via Rulebook.
  B. Endpoint HANGS        -> per-call timeout must abort.
  C. Endpoint FLAKY        -> circuit trips after threshold; retries capped.
  D. Endpoint DRIFTED      -> single failure returns a clear message; no
                              storm because InvocationBudget caps retries.

For each scenario we run the SAME task twice: once with raw MCP tools
bound, once with Graxella guardrails (CircuitBreaker + InvocationBudget
+ timeout + rulebook substitute dispatch) applied. We measure wall time,
total LLM steps, unique tools called, whether the substitute was
dispatched, and the stop reason.

The point is not that the guarded run always produces a perfect user
answer — the point is that the guarded run BOUNDS the damage. An MCP
outage becomes a fast, cheap, structured 'this call failed, move on'
instead of a runaway loop that eats context window and wall clock.
"""
from __future__ import annotations

from pathlib import Path

from langchain_ollama import ChatOllama

from graxella import Proposal, Rulebook

from progressive_skills.guardrails import (CircuitBreaker, GuardEvent,
                                           InvocationBudget, guarded_wrap)
from progressive_skills.mcp_tools import (MCP_TOOLS_BY_NAME, UNGUARDED_TOOLS,
                                          mcp_search_hotels_backup)
from progressive_skills.react import react_run


MODEL = "qwen2.5:3b"


def _llm_factory():
    return lambda: ChatOllama(model=MODEL, temperature=0)


SCENARIOS = [
    ("A. DOWN endpoint (hotels_down)",
     "Find a hotel in Paris for check-in 2026-04-20 check-out 2026-04-23. "
     "The user needs a hotel — do not stop until you have real hotel options."),
    ("B. HANGING endpoint (currency_hangs)",
     "Convert 500 EUR to JPY. Give me the answer."),
    ("C. FLAKY endpoint (translate_flaky)",
     "Translate 'thank you' into Japanese."),
    ("D. DRIFTED endpoint (visa_drifted)",
     "Do I need a visa to travel from US to Japan?"),
]


def _make_rulebook(path: Path) -> Rulebook:
    """Promote a hotels_down -> hotels_backup substitution.

    In production this rule would come from DocsMiner (the operator's runbook
    that lists failover providers) or from RuleDistiller (mining the pattern
    from ok/err episode pairs). Here we hand-craft it in one place.
    """
    if path.exists():
        path.unlink()
    rb = Rulebook(path=path)
    rb.promote(Proposal(
        id="prop_mcp_hotels_failover",
        kind="rule",
        subject="mcp:mcp_search_hotels_down->mcp_search_hotels_backup",
        change={
            "if_intent": None,   # any intent
            "replace_skill": "mcp_search_hotels_down",
            "with_skill": "mcp_search_hotels_backup",
            "recipe": {},        # identical schema; no arg rename needed
        },
        evidence="hotels primary MCP endpoint down; backup provider live with same schema",
        derived_from=["ops:runbook.md"],
        confidence=1.0,
    ), approved_by="ops@graxella")
    return rb


def _summarize(trace, events: list[GuardEvent] | None) -> dict:
    called = trace.tools_called_flat()
    unique = sorted(set(called))
    substituted = [e for e in (events or []) if e.outcome.value == "substituted"]
    return {
        "wall_s": trace.wall_s,
        "llm_steps": trace.total_llm_calls,
        "tool_calls_total": len(called),
        "unique_tools": unique,
        "stop_reason": trace.stop_reason,
        "substituted": [f"{e.tool}->{e.substituted_with}" for e in substituted],
    }


def _ascii(s: str) -> str:
    """Windows cp1252 chokes on Japanese/emoji. Strip to ASCII for the console."""
    return s.encode("ascii", "replace").decode("ascii")


def _print_row(label: str, s: dict, trace=None) -> None:
    subs = ",".join(s["substituted"]) or "-"
    unique = ",".join(s["unique_tools"]) or "-"
    print(f"  {label:<12} wall={s['wall_s']:>5.2f}s  steps={s['llm_steps']:>2}  "
          f"tool_calls={s['tool_calls_total']:>2}  stop={s['stop_reason']:<10}  "
          f"subs={subs}")
    print(f"               tools called: {unique}")
    if trace is not None:
        for st in trace.steps:
            if st.tool_calls:
                previews = " | ".join(
                    f"{n}: {_ascii(r or '')[:60]!r}"
                    for n, r in zip(st.tool_calls, st.tool_results)
                )
                print(f"               step {st.step} ({st.wall_s}s): {previews}")


def run() -> None:
    print("=" * 96)
    print("MCP resilience — unguarded vs Graxella-guarded")
    print("=" * 96)

    rulebook_path = Path(__file__).parent / "_artifacts_mcp_rulebook.json"
    rb = _make_rulebook(rulebook_path)
    print(f"Rulebook   : {rulebook_path.name} ({len(list(rb.all_rules()))} rule)")
    print(f"Guardrails : CircuitBreaker(threshold=2, cooldown=30s) + "
          f"InvocationBudget(2/tool) + timeout=3s")
    print(f"Model      : {MODEL}\n")

    for title, query in SCENARIOS:
        print("-" * 96)
        print(f"{title}")
        print(f"  query: {query}")

        # Unguarded — raw MCP tools, no protection.
        trace_u = react_run(_llm_factory(), list(UNGUARDED_TOOLS),
                            query, max_steps=6)
        s_u = _summarize(trace_u, events=None)
        _print_row("UNGUARDED", s_u, trace=trace_u)

        # Guarded — same tools plus the backup, wrapped in guardrails.
        # Context-managed so `.invoke` patches are strictly scoped to this
        # block; no leakage into the next scenario's unguarded run.
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=30.0)
        budget = InvocationBudget(max_calls_per_tool=2)
        events: list[GuardEvent] = []
        with guarded_wrap(
            list(UNGUARDED_TOOLS),
            breaker=breaker,
            budget=budget,
            rulebook=rb,
            intent=None,
            timeout_s=3.0,
            events=events,
            substitute_pool={**MCP_TOOLS_BY_NAME,
                             "mcp_search_hotels_backup": mcp_search_hotels_backup},
        ) as guarded:
            trace_g = react_run(_llm_factory(), guarded, query, max_steps=6)
        s_g = _summarize(trace_g, events=events)
        _print_row("GUARDED", s_g, trace=trace_g)

    print("-" * 96)
    print("\nRead the delta: UNGUARDED wall/steps balloon when the MCP endpoint")
    print("misbehaves; GUARDED bounds the damage in every scenario. Scenario A")
    print("additionally shows the rulebook dispatching hotels_down -> hotels_backup")
    print("so the user still gets real hotel options despite the outage.")


if __name__ == "__main__":
    run()
