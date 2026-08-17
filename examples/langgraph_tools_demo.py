"""Phase-2 demo — semantic tool discovery + self-healing on a LangGraph app.

Adds two capabilities on top of Phase 1:

  1. `graxella.langgraph.tools.catalog(...)` — ranks a set of LangChain tools
     by relevance to a query string. Deterministic, zero heavy deps.
     Backend swap to B8's KnowledgeGraph is a one-arg change.

  2. `graxella.langgraph.heal.wrap(...)` — wraps a tool so a failure gets
     transparently retried against a fallback tool with args translated by
     a TransformRecipe (the shape B10's TransformCompiler emits).

The whole thing still records ExperienceEpisodes via the Phase-1 callback,
and the hidden agenda still mines a substitution rule from the healed
attempts — proving the intelligence loop is orchestrator-agnostic AND
resilient to tool drift.

Success criteria:
  * Semantic discovery: query 'temperature outside' picks up fetch_forecast
    even though the word 'forecast' isn't a synonym of 'temperature'.
  * Self-heal: LangGraph node calls heal.wrap(v1, fallback=v2, recipe=...);
    v1 raises, v2 gets the healed args, the graph returns a valid result.
  * Experience row shows both tool calls (v1=ERR, v2=ok).
  * Hidden agenda proposes the get_weather -> fetch_forecast rule.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TypedDict

_REPO = Path(__file__).resolve().parents[2]
for _sub in ("graxella",):
    p = _REPO / _sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from langchain_core.runnables import RunnableConfig  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402

from graxella import SqliteExperienceStore  # noqa: E402
from graxella.agenda import HiddenAgendaRunner  # noqa: E402
from graxella.discovery import catalog  # noqa: E402
from graxella.healing import recipes as heal  # noqa: E402
from graxella.integrations.langgraph import GraxellaCallback  # noqa: E402


# --- tools --------------------------------------------------------- --------
@tool
def get_weather(city: str) -> str:
    """Fetch a current weather report for the given city name."""
    raise RuntimeError("weather_v1 drift: 'city' arg rejected by upstream")


@tool
def fetch_forecast(location: str) -> str:
    """Fetch current weather forecast (temperature, sky) for a location."""
    return f"forecast for {location}: sunny, 27C"


@tool
def stock_price(ticker: str) -> str:
    """Return the latest traded price of a stock ticker."""
    return f"{ticker} 42.10"


@tool
def news_headlines(topic: str) -> str:
    """Return recent news headlines about a topic."""
    return f"latest on {topic}: nothing new"


ALL_TOOLS = [get_weather, fetch_forecast, stock_price, news_headlines]


# --- graph ----------------------------------------------------------------
class State(TypedDict):
    query: str
    result: str


CAT = catalog(ALL_TOOLS)  # built once — deterministic, no heavy deps

# One known drift migration: legacy get_weather(city) -> fetch_forecast(location).
# In production this recipe would come from B10's TransformCompiler (offline),
# not be hand-written. Same shape, same runtime behaviour.
HEALED_WEATHER = heal.wrap(
    get_weather, fallback=fetch_forecast,
    recipe=heal.TransformRecipe(field_map={"city": "location"}),
)

# Per-tool arg key each demo tool actually wants.
_ARG_KEY = {
    "get_weather":    "city",
    "fetch_forecast": "location",
    "stock_price":    "ticker",
    "news_headlines": "topic",
}


def dispatch(state: State, config: RunnableConfig) -> State:
    """One node — pick a tool for the query, invoke it (self-heal on drift).

    Kept small on purpose: the two Phase-2 surfaces (`catalog.find` and
    `heal.wrap`) are the two lines that matter.
    """
    top = CAT.find(state["query"], k=1)
    if not top:
        return {**state, "result": "no tool matched"}

    primary = top[0]
    arg_key = _ARG_KEY[primary.name]
    payload = {arg_key: state["query"]}

    # Only the legacy weather tool has a known drift recipe; everything else
    # runs unwrapped.
    if primary.name == "get_weather":
        r = HEALED_WEATHER(payload, config=config)
    else:
        r = primary.invoke(payload, config=config)
    return {**state, "result": str(r)}


def build_app():
    b = StateGraph(State)
    b.add_node("dispatch", dispatch)
    b.set_entry_point("dispatch")
    b.add_edge("dispatch", END)
    return b.compile()


# --- run ------------------------------------------------------------------
def main() -> None:
    print("=== Semantic discovery (lite backend) ===")
    cat = catalog(ALL_TOOLS)
    for q in ["temperature outside",
              "market price",
              "what's happening in AI",
              "current weather in Bengaluru"]:
        ranked = cat.find(q, k=2)
        names = ", ".join(t.name for t in ranked) or "(none)"
        print(f"  query={q!r:35}  -> {names}")

    store_path = Path(__file__).parent / "experience_phase2.db"
    if store_path.exists():
        store_path.unlink()
    store = SqliteExperienceStore(store_path)

    app = build_app()

    print("\n=== Running LangGraph app with GraxellaCallback + heal.wrap ===")
    queries = [
        "current weather in Bengaluru",
        "current weather in Chennai",
        "current weather in Mumbai",
        "market price of INFY",
        "latest headlines on AI",
    ]
    for i, q in enumerate(queries, 1):
        cb = GraxellaCallback(
            store=store, session_id=f"lg-tools-{i}",
            default_intent="tool_dispatch",
        )
        out = app.invoke({"query": q, "result": ""},
                         config={"callbacks": [cb],
                                 "metadata": {"intent": "tool_dispatch"}})
        print(f"  run {i}: {q!r:35} -> {out['result'][:60]}")

    print("\n=== Experience rows ===")
    for e in store.all():
        tcs = " | ".join(f"{tc.tool_id}={'ok' if tc.ok else 'ERR'}"
                         for tc in e.tool_calls) or "(no tools)"
        print(f"  {e.id[:14]} ok={e.ok!s:5} tools=[{tcs}]")

    print("\n=== Hidden Agenda proposals ===")
    for p in HiddenAgendaRunner(store=store).run():
        print(f"\n  [{p.kind}] {p.subject}  (conf={p.confidence:.2f})")
        print(f"     evidence     : {p.evidence}")
        print(f"     derived_from : {p.derived_from}")

    store.close()
    print(f"\nSQLite file: {store_path}")


if __name__ == "__main__":
    main()
