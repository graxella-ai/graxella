"""Phase-5 demo — one-line magic + router node.

Two beats:

  1. graxella.wrap(...) — take a vanilla LangGraph app that the dev already
     has, hand back a smart_app with the same `.invoke()` signature. From
     that call:
       * every run becomes an Episode (Phase 1 observability)
       * every tool `.invoke` consults the rulebook first (Phase 3 bypass)
       * if `docs=` is supplied, DocsMiner runs at wrap-time and prints
         reviewable proposals to stderr (Phase 4 pre-traffic intelligence)

  2. router / route_by_query — a deterministic B11-style dispatcher node.
     No LLM, sub-millisecond, chooses which specialist handles a query
     using human-authored descriptions.

The demo shows a multi-intent app (weather + market + news) where:
   - The router node picks the right destination from the query.
   - Each destination has its own tool.
   - The weather destination's tool has a promoted rulebook substitution
     applied transparently by wrap() — no explicit heal.route() in user code.
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

import graxella  # noqa: E402
from graxella import Rulebook, SqliteExperienceStore  # noqa: E402
from graxella.agenda import DocsMiner  # noqa: E402
from graxella.knowledge import from_docs  # noqa: E402
from graxella.routing import route_by_query, router  # noqa: E402


# --- tools ----------------------------------------------------------------
@tool
def get_weather(city: str) -> str:
    """Legacy v1 weather tool. Deprecated — always fails."""
    raise RuntimeError("weather_v1 drift: 'city' arg rejected by upstream")


@tool
def fetch_forecast(location: str) -> str:
    """v2 weather tool — documented successor to get_weather."""
    return f"forecast for {location}: sunny, 27C"


@tool
def stock_price(ticker: str) -> str:
    """Latest traded price of a stock ticker."""
    return f"{ticker.upper()} 42.10"


@tool
def news_headlines(topic: str) -> str:
    """Recent news headlines on a topic."""
    return f"latest on {topic}: nothing new"


ALL_TOOLS = [get_weather, fetch_forecast, stock_price, news_headlines]

# --- router ---------------------------------------------------------------
ROUTES = router([
    {"name": "weather",
     "description": "current forecast, temperature, sky, rain, weather in a city"},
    {"name": "market",
     "description": "stock price, ticker, market data, quote, share value"},
    {"name": "news",
     "description": "news headlines, latest stories on a topic"},
])


# --- graph ----------------------------------------------------------------
class State(TypedDict):
    query: str
    __route__: str
    result: str


def route_node(state: State, config: RunnableConfig) -> State:
    return route_by_query(ROUTES)(state, config)  # type: ignore[return-value]


def weather_node(state: State, config: RunnableConfig) -> State:
    r = get_weather.invoke({"city": state["query"]}, config=config)
    return {**state, "result": str(r)}


def market_node(state: State, config: RunnableConfig) -> State:
    r = stock_price.invoke({"ticker": state["query"]}, config=config)
    return {**state, "result": str(r)}


def news_node(state: State, config: RunnableConfig) -> State:
    r = news_headlines.invoke({"topic": state["query"]}, config=config)
    return {**state, "result": str(r)}


def none_node(state: State, config: RunnableConfig) -> State:
    return {**state, "result": "no destination matched"}


def build_app():
    b = StateGraph(State)
    b.add_node("route", route_node)
    b.add_node("weather", weather_node)
    b.add_node("market", market_node)
    b.add_node("news", news_node)
    b.add_node("none", none_node)
    b.set_entry_point("route")
    b.add_conditional_edges(
        "route",
        lambda s: s.get("__route__") or "none",
        {"weather": "weather", "market": "market", "news": "news", "none": "none"},
    )
    for n in ("weather", "market", "news", "none"):
        b.add_edge(n, END)
    return b.compile()


# --- run ------------------------------------------------------------------
QUERIES = [
    "temperature in Bengaluru",
    "share price of INFY",
    "latest on AI safety",
    "current weather in Chennai",
    "stock quote for TCS",
]


def main() -> None:
    here = Path(__file__).parent
    docs_dir = here / "sample_docs"          # reused from Phase 4
    store_path = here / "experience_phase5.db"
    rulebook_path = here / "rulebook_phase5.json"
    for p in (store_path, rulebook_path):
        if p.exists():
            p.unlink()

    # -- 5.1 — first show the raw router picking destinations -----------
    print("=== Router top-1 picks (deterministic, zero LLM) ===")
    for q in QUERIES:
        top = ROUTES.top_k(q, k=1)
        picked = top[0][0] if top else "(none)"
        score = top[0][1] if top else 0.0
        print(f"  {q!r:35}  -> {picked:8}  (score={score:.2f})")

    # -- 5.2 — seed rulebook by promoting the docs proposal --------------
    # (In real life a human uses the CLI. We inline it here so the demo
    # can show the wrapped app's rulebook bypass in action.)
    print("\n=== Bootstrapping rulebook from docs (Phase-4 pipeline) ===")
    seed = from_docs(docs_dir)
    proposals = DocsMiner(seed=seed, default_intent="weather_lookup").mine([])
    rulebook = Rulebook(path=rulebook_path)
    for p in proposals:
        if (p.change.get("replace_skill") == "get_weather"
                and p.change.get("with_skill") == "fetch_forecast"):
            rule = rulebook.promote(p, approved_by="ram@graxella")
            print(f"  promoted {p.id} -> {rule.id}")

    # -- 5.3 — vanilla LangGraph app -----------------------------------
    app = build_app()

    # -- 5.4 — ONE-LINE MAGIC ------------------------------------------
    print("\n=== graxella.wrap(app, ...) — one line, full intelligence ===")
    store = SqliteExperienceStore(store_path)
    smart_app = graxella.wrap(
        app,
        tools=ALL_TOOLS,
        store=store,
        rulebook=rulebook_path,
        docs=docs_dir,              # prints reviewable proposals at wrap-time
        intent="dispatch",
    )

    print("\n=== Running the wrapped app ===")
    for i, q in enumerate(QUERIES, 1):
        out = smart_app.invoke({"query": q, "__route__": "", "result": ""})
        print(f"  run {i}: {q!r:35} -> {out['result'][:60]}")

    print("\n=== Episodes (weather runs must show fetch_forecast=ok only) ===")
    for e in store.all():
        tcs = " | ".join(f"{tc.tool_id}={'ok' if tc.ok else 'ERR'}"
                         for tc in e.tool_calls) or "(no tools)"
        print(f"  {e.id[:14]}  session={e.session_id:8} tools=[{tcs}]")

    store.close()
    print("\ndone. wrap() gave observability + rulebook bypass with zero "
          "changes to the underlying node code.")


if __name__ == "__main__":
    main()
