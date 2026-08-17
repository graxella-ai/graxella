"""Multi-agent trip-planner as a LangGraph StateGraph.

Nodes:
    planner    — canned lookup that turns the free-form request into a plan
                 dict. In production this would be an LLM extractor; kept
                 deterministic here so the demo output is reproducible.
    flights    — invokes search_flights with args pulled from state.plan.
    hotels     — invokes search_hotels_v1 (the drifted API).
    activities — invokes search_activities.
    itinerary  — LLM composes a day-by-day Markdown itinerary from the three
                 specialist outputs.

Edges are linear: planner -> flights -> hotels -> activities -> itinerary.
This is the shape both notebooks share verbatim. The Graxella notebook
wraps the compiled app with graxella.wrap(...) — no other change to this
module is needed.

The hotels_node deliberately assembles ALL THREE args (location + checkin +
checkout) even though it targets the v1 tool. That way when Graxella's
rulebook rewrites `location -> city` and dispatches to find_accommodations,
the two extra args pass through untouched and the successor gets everything
it needs. A pure field_map recipe is enough — no per-call state closures.
"""
from __future__ import annotations

import json
import time
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from trip_planner.tools import TOOLS_BY_NAME


MODEL = "qwen2.5:3b"


class TripState(TypedDict, total=False):
    request: str
    plan: dict
    flights: str
    hotels: str
    activities: str
    itinerary: str
    _errors: list
    _timings: dict


def _llm():
    from langchain_ollama import ChatOllama
    return ChatOllama(model=MODEL, temperature=0)


# --- planner -----------------------------------------------------------------
CANNED_PLANS: dict[str, dict[str, str]] = {
    "3-day cultural trip to Paris from London leaving 2026-04-20": {
        "origin": "London", "destination": "Paris",
        "start_date": "2026-04-20", "end_date": "2026-04-23",
        "interests": "culture, museums, history",
    },
    "Weekend beach getaway to Goa from Bengaluru on 2026-05-01": {
        "origin": "Bengaluru", "destination": "Goa",
        "start_date": "2026-05-01", "end_date": "2026-05-03",
        "interests": "beach, seafood, relaxation",
    },
    "5-day food + temples trip to Kyoto from Tokyo starting 2026-04-25": {
        "origin": "Tokyo", "destination": "Kyoto",
        "start_date": "2026-04-25", "end_date": "2026-04-30",
        "interests": "food, temples, museums",
    },
}
SAMPLE_QUERIES = list(CANNED_PLANS.keys())


def planner_node(state: TripState, config: RunnableConfig) -> TripState:
    t0 = time.time()
    plan = CANNED_PLANS.get(state["request"], {
        "origin": "Unknown", "destination": "Unknown",
        "start_date": "2026-04-15", "end_date": "2026-04-18",
        "interests": "sightseeing",
    })
    timings = dict(state.get("_timings", {}))
    timings["planner"] = round(time.time() - t0, 3)
    return {**state, "plan": plan, "_timings": timings, "_errors": []}


# --- specialists -------------------------------------------------------------
def _dispatch(state: TripState, node: str, tool_name: str,
              args: dict[str, Any], config: RunnableConfig) -> TripState:
    t0 = time.time()
    errors = list(state.get("_errors", []))
    tool = TOOLS_BY_NAME[tool_name]
    try:
        result = tool.invoke(args, config=config)
    except Exception as e:
        errors.append({"node": node, "tool": tool_name,
                       "err_type": type(e).__name__, "err": str(e)})
        result = f"({node} unavailable: {type(e).__name__})"
    timings = dict(state.get("_timings", {}))
    timings[node] = round(time.time() - t0, 3)
    return {**state, node: str(result), "_errors": errors, "_timings": timings}


def flights_node(state: TripState, config: RunnableConfig) -> TripState:
    p = state["plan"]
    return _dispatch(state, "flights", "search_flights",
                     {"origin": p["origin"], "destination": p["destination"],
                      "date": p["start_date"]}, config)


def hotels_node(state: TripState, config: RunnableConfig) -> TripState:
    """Legacy hotels tool. The LLM was trained on v1 — but v1 is sunset."""
    p = state["plan"]
    return _dispatch(state, "hotels", "search_hotels_v1",
                     {"location": p["destination"],
                      "checkin": p["start_date"],
                      "checkout": p["end_date"]}, config)


def activities_node(state: TripState, config: RunnableConfig) -> TripState:
    p = state["plan"]
    return _dispatch(state, "activities", "search_activities",
                     {"city": p["destination"],
                      "interests": p.get("interests", "sightseeing")}, config)


def itinerary_node(state: TripState, config: RunnableConfig) -> TripState:
    t0 = time.time()
    llm = _llm()
    system = SystemMessage(content=(
        "You are a travel concierge. Compose a friendly day-by-day itinerary "
        "in Markdown using the flights, hotels, and activities below. If any "
        "section says 'unavailable', mention that limitation and continue "
        "with the rest. Keep it under 250 words. Use bullet points."
    ))
    user = HumanMessage(content=(
        f"Trip plan: {json.dumps(state['plan'])}\n\n"
        f"FLIGHTS:\n{state.get('flights', '(missing)')}\n\n"
        f"HOTELS:\n{state.get('hotels', '(missing)')}\n\n"
        f"ACTIVITIES:\n{state.get('activities', '(missing)')}"
    ))
    resp = llm.invoke([system, user])
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    timings = dict(state.get("_timings", {}))
    timings["itinerary"] = round(time.time() - t0, 3)
    return {**state, "itinerary": text, "_timings": timings}


# --- graph -------------------------------------------------------------------
def build_app():
    b = StateGraph(TripState)
    b.add_node("planner", planner_node)
    b.add_node("flights", flights_node)
    b.add_node("hotels", hotels_node)
    b.add_node("activities", activities_node)
    b.add_node("itinerary", itinerary_node)
    b.set_entry_point("planner")
    b.add_edge("planner", "flights")
    b.add_edge("flights", "hotels")
    b.add_edge("hotels", "activities")
    b.add_edge("activities", "itinerary")
    b.add_edge("itinerary", END)
    return b.compile()


# --- run helpers -------------------------------------------------------------
def run_batch(app, queries: list[str] | None = None,
              config: dict | None = None) -> list[tuple[str, TripState, float]]:
    """Run each query end-to-end. Returns [(request, final_state, wall_seconds)]."""
    if queries is None:
        queries = SAMPLE_QUERIES
    out: list[tuple[str, TripState, float]] = []
    for q in queries:
        t0 = time.time()
        state = app.invoke({"request": q}, config=config or {})
        out.append((q, state, round(time.time() - t0, 3)))
    return out


def summarize(results: list[tuple[str, TripState, float]]) -> list[dict[str, Any]]:
    """Flatten a batch into per-run metrics rows suitable for a DataFrame."""
    rows: list[dict[str, Any]] = []
    for q, state, wall in results:
        errors = state.get("_errors") or []
        hotels_txt = (state.get("hotels", "") or "")
        rows.append({
            "trip": q[:38] + ("..." if len(q) > 38 else ""),
            "wall_s": wall,
            "tool_errors": len(errors),
            "hotels_ok": "unavailable" not in hotels_txt.lower(),
            "hotels_preview": hotels_txt.split("\n")[0][:60],
        })
    return rows


def check_ollama(model: str = MODEL) -> bool:
    """Ping Ollama with a 1-token request. Returns True if reachable."""
    try:
        from langchain_ollama import ChatOllama
        ChatOllama(model=model, temperature=0, num_predict=1).invoke("hi")
        return True
    except Exception as e:
        print(f"[!] Ollama check failed: {type(e).__name__}: {e}")
        print(f"    Ensure `ollama serve` is running and `{model}` is pulled:")
        print(f"    ollama pull {model}")
        return False
