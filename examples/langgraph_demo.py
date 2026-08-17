"""Phase-1 demo — a real LangGraph app + GraxellaCallback + SQLite.

Golden path: a two-tool weather app that reproduces the same "v1 breaks,
v2 works" pattern used in the AXON demo — but this time run through a
plain LangGraph StateGraph, with zero AXON code. The Graxella callback
captures each chain run as one ExperienceEpisode; the SqliteExperienceStore
persists them for the hidden agenda to mine later.

Success criteria:
  * `python examples/langgraph_demo.py` runs end-to-end.
  * `./experience.db` is created and contains one row per app.invoke().
  * Each row shows tool_calls (with ok=False for get_weather, ok=True for
    fetch_forecast).
  * The hidden agenda run at the bottom mines the same substitution rule
    the AXON demo did — proving the store schema is orchestrator-agnostic.
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
from graxella.integrations.langgraph import GraxellaCallback  # noqa: E402


class State(TypedDict):
    task: str
    strategy: str  # "pair" -> try v1 then v2; "v2" -> v2 only
    result: str


@tool
def get_weather(city: str) -> str:
    """Legacy weather API (v1). Schema drift: the 'city' arg no longer works."""
    raise RuntimeError("weather_v1 drift: 'city' arg rejected by upstream")


@tool
def fetch_forecast(location: str) -> str:
    """Current weather API (v2). Uses 'location' instead of 'city'."""
    return f"forecast for {location}: sunny, 27C"


def dispatch(state: State, config: RunnableConfig) -> State:
    """Single node — routes by strategy so we can drive both patterns.

    Tools are invoked with `config=config` so LangChain's callback context
    propagates from app.invoke() down to on_tool_start/end.
    """
    task = state["task"]
    if state["strategy"] == "pair":
        try:
            r = get_weather.invoke({"city": task}, config=config)
            return {**state, "result": r}
        except Exception:
            pass
    r = fetch_forecast.invoke({"location": task}, config=config)
    return {**state, "result": r}


def build_app():
    b = StateGraph(State)
    b.add_node("dispatch", dispatch)
    b.set_entry_point("dispatch")
    b.add_edge("dispatch", END)
    return b.compile()


def main() -> None:
    store_path = Path(__file__).parent / "experience.db"
    if store_path.exists():
        store_path.unlink()  # fresh run
    store = SqliteExperienceStore(store_path)

    app = build_app()
    cb = GraxellaCallback(
        store=store, session_id="lg-demo-phase1",
        default_intent="weather_lookup",
    )
    cfg = {"callbacks": [cb], "metadata": {"intent": "weather_lookup"}}

    # 3 "pair" runs so RuleDistiller has multi-session support for its rule,
    # plus 2 pure-v2 runs so CapabilityReweigher gets a clean signal.
    runs = [
        ("bengaluru", "pair"),
        ("chennai",   "pair"),
        ("mumbai",    "pair"),
        ("delhi",     "v2"),
        ("kolkata",   "v2"),
    ]
    print("=== Running LangGraph app ===")
    for i, (city, strategy) in enumerate(runs, 1):
        # Fresh session per run so RuleDistiller sees independent evidence.
        cb.session_id = f"lg-demo-{i}"
        out = app.invoke(
            {"task": city, "strategy": strategy, "result": ""}, config=cfg,
        )
        print(f"  run {i}: strategy={strategy:5} city={city:10} -> "
              f"{out['result'][:60]}")

    # --- read back from SQLite -------------------------------------------
    print("\n=== ExperienceEpisodes in ./experience.db ===")
    for e in store.all():
        tcs = " | ".join(f"{tc.tool_id}={'ok' if tc.ok else 'ERR'}"
                         for tc in e.tool_calls) or "(no tools)"
        print(f"  {e.id[:14]}  intent={e.intent:16}  ok={e.ok!s:5}"
              f"  tools=[{tcs}]")

    # --- run the hidden agenda on the SQLite-backed store ----------------
    print("\n=== Hidden Agenda proposals ===")
    proposals = HiddenAgendaRunner(store=store).run()
    if not proposals:
        print("  (none - RuleDistiller needs the same substitution across "
              ">=2 sessions)")
    for p in proposals:
        print(f"\n  [{p.kind}] {p.subject}  (conf={p.confidence:.2f})")
        print(f"     evidence     : {p.evidence}")
        print(f"     derived_from : {p.derived_from}")

    store.close()
    print(f"\nSQLite file: {store_path}")


if __name__ == "__main__":
    main()
