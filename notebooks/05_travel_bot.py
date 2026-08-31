"""Showcase 05 -- travel booking bot (flights + hotels) on graxella.

A small, realistic multi-agent system. Two agents, four tools, one
mesh -- and every graxella superpower comes for free. No JSON
schemas, no handoff protocols, no A2A envelope wiring. That's the
whole thesis: silent plumbing.

What you WRITE:
  * 4 @tool functions
  * 2 agents (one native LangGraph, one graxella.Agent -- both work)
  * 1 constitution invariant (optional)
  * 1 gate policy    (optional)
  * ONE call: graxella.mesh([...])

What graxella HANDLES silently:
  * routing (TF-IDF picks the right agent for each user turn)
  * peer-directory injection (each agent knows what the other can do)
  * memory (every booking + decision goes into mnema, cross-turn recall)
  * constitution (invariants fire on every routed task, detection-only)
  * gate (learning proposals scored + auto-approved/rejected)
  * why() (cross-source provenance for any decision)

Story: user has a 5-turn conversation with the travel bot. Each turn
routes to the right agent. Booking IDs persist across turns via mnema.

Prereqs: Ollama daemon running with ``qwen2.5:3b`` pulled.
Run:
    .venv/Scripts/python showcase/05_travel_bot.py   (Windows)
    .venv/bin/python     showcase/05_travel_bot.py   (macOS/Linux)
"""
from __future__ import annotations

import sys
import tempfile
import uuid
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "graxella"))

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

import graxella
from graxella import (Agent, Constitution, GatePolicy, Memory, ObjectiveScores,
                      PromotionGate, UnifiedTracer)


# ============ tools ================================================
# Mock backends. In a real system these hit Amadeus / Skyscanner /
# Booking.com. Return shapes are what the LLM actually sees.

_FLIGHT_CATALOG = {
    ("NYC", "PAR", "2026-12-15"): [
        {"id": "AF012", "carrier": "Air France", "price_usd": 720, "depart": "18:30", "duration": "7h 25m"},
        {"id": "DL264", "carrier": "Delta",       "price_usd": 685, "depart": "21:15", "duration": "7h 40m"},
        {"id": "UA057", "carrier": "United",      "price_usd": 899, "depart": "17:00", "duration": "7h 10m"},
    ],
}
_HOTEL_CATALOG = {
    ("paris", "eiffel tower"): [
        {"id": "HP-9021", "name": "Hotel Le Marais",     "nightly_usd": 189, "walk_min": 22, "rating": 4.4},
        {"id": "HP-1188", "name": "Champ de Mars Suites", "nightly_usd": 275, "walk_min": 4,  "rating": 4.6},
        {"id": "HP-4402", "name": "Trocadero Boutique",  "nightly_usd": 219, "walk_min": 11, "rating": 4.5},
    ],
}


@tool
def search_flights(origin: str, destination: str, date: str) -> list:
    """Search flights for a given origin, destination, and date (YYYY-MM-DD).

    Args:
        origin: 3-letter city code (e.g. NYC).
        destination: 3-letter city code (e.g. PAR).
        date: departure date in YYYY-MM-DD format.
    """
    key = (origin.upper(), destination.upper(), date)
    return _FLIGHT_CATALOG.get(key, [])


@tool
def book_flight(flight_id: str, passenger_name: str) -> dict:
    """Book a specific flight by its id for a named passenger. Returns
    the booking record (with a fresh confirmation code)."""
    return {
        "confirmation": f"CONF-{uuid.uuid4().hex[:8].upper()}",
        "flight_id": flight_id,
        "passenger": passenger_name,
        "status": "confirmed",
    }


@tool
def search_hotels(city: str, near_landmark: str) -> list:
    """Search hotels in a city near a specific landmark.

    Args:
        city: city name (e.g. Paris).
        near_landmark: landmark to search near (e.g. Eiffel Tower).
    """
    key = (city.lower().strip(), near_landmark.lower().strip())
    return _HOTEL_CATALOG.get(key, [])


@tool
def book_hotel(hotel_id: str, nights: int, guest_name: str) -> dict:
    """Book a specific hotel by its id for N nights under a guest name."""
    return {
        "confirmation": f"HTL-{uuid.uuid4().hex[:8].upper()}",
        "hotel_id": hotel_id,
        "nights": nights,
        "guest": guest_name,
        "status": "confirmed",
        "requires_review": True,   # trips the constitution invariant
    }


# ============ agents ==============================================

def section(t: str) -> None:
    print(f"\n{'-' * 76}\n{t}\n{'-' * 76}")


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="graxella-travel-"))
    print(f"[setup] workdir: {workdir}")
    print(f"[setup] LLM:     Ollama qwen2.5:3b (must be listening on localhost:11434)")

    llm = ChatOllama(model="qwen2.5:3b", temperature=0)

    # (1) --- Two agents, different shapes. Both first-class citizens.
    section("(1) AGENTS -- native LangGraph + graxella.Agent, one mesh")

    # Path A: native LangGraph.
    flight_agent = create_react_agent(
        llm,
        tools=[search_flights, book_flight],
        name="flights",
    )

    # Path B: graxella.Agent (CrewAI-shape). Same wire underneath.
    hotel_agent = Agent(
        role="hotels",
        goal="search and book hotels near landmarks the guest asks about",
        backstory=("You help travellers find and book hotels. Always call "
                   "search_hotels first to see options, then book_hotel when "
                   "the guest picks one. Keep responses short."),
        tools=[search_hotels, book_hotel],
        llm=llm,
    )

    print("  flights = create_react_agent(llm, [search_flights, book_flight], name='flights')")
    print("  hotels  = graxella.Agent(role='hotels', tools=[search_hotels, book_hotel], llm=llm)")

    # (2) --- One line wires memory + routing + governance ------------
    section("(2) WRAP -- graxella.mesh([flights, hotels], memory=..., router='tfidf')")

    memory = Memory.sqlite(db_path=str(workdir / "mnema.db"),
                           agent_id="travel-bot")
    tracer = UnifiedTracer.default()
    gate = PromotionGate(
        threshold=0.85, require_human=True,
        policy=GatePolicy(
            weights={"quality": 0.4, "compliance": 0.3, "cost": 0.2, "latency": 0.1},
            cost_reference=0.10, latency_reference=500.0,
            compliance_floor=0.9, auto_approve=0.85, needs_human_min=0.5,
        ),
    )
    constitution = Constitution.from_dict({
        "version": "1.0",
        "invariants": [{
            "name": "hotel_bookings.require_confirmation",
            "applies_to": "delegate", "severity": "warning",
            "predicate": {
                "type": "object",
                "properties": {
                    "chosen_agent": {"not": {"const": "hotels"}},
                },
            },
        }],
    })

    bot = graxella.mesh(
        [flight_agent, hotel_agent],
        memory=memory,
        tracer=tracer,
        gate=gate,
        constitution=constitution,
        router="tfidf",
        store_path=str(workdir / "routes.jsonl"),
    )
    print(f"  agents registered: {bot.society.agents()}")
    print("  (hotels' system prompt now includes 'flights' as a peer, and vice versa)")

    # (3) --- 5-turn conversation. TF-IDF routes each turn to the
    #         right agent; graxella persists everything to memory.
    section("(3) CONVERSATION -- 5 real user turns")
    turns = [
        "Find flights from NYC to PAR on 2026-12-15",
        "Book flight DL264 for John Smith",
        "Find hotels in Paris near the Eiffel Tower",
        "Book hotel HP-9021 for 5 nights for John Smith",
        "Actually, book HP-1188 for 3 nights instead for John Smith",
    ]
    decision_ids: list[str] = []
    for i, msg in enumerate(turns, 1):
        print(f"\n  turn {i}> {msg}")
        out = bot.invoke({"messages": [("user", msg)]})
        route = out["route"]
        print(f"    routed -> {route['agent']}  score={route['score']:.3f}"
              f"  strategy={route['strategy']}")
        content = out["messages"][-1]["content"]
        snip = content if len(content) < 200 else content[:200] + "..."
        print(f"    bot:    {snip}")
        if route.get("decision_id"):
            decision_ids.append(route["decision_id"])

    # (4) --- constitution fires whenever hotels is picked ------------
    section("(4) CONSTITUTION -- 'hotels' pick trips 'require_confirmation'")
    viols = tracer.events(event_type="governance.constitution_violation")
    print(f"  {len(viols)} violation(s) recorded (detection-only, bookings still went through)")
    for v in viols[:3]:
        p = v.payload
        print(f"    [{p['severity']}] {p['name']}")

    # (5) --- gate scores travel-domain learning proposals ------------
    section("(5) GATE -- three travel-domain proposals")
    proposals = [
        # 1. Narrow, high quality, full compliance -> AUTO_APPROVE
        ("route.tag_add",
         {"agent": "hotels", "add_terms": ["vacation", "trip", "stay"]},
         "narrow",
         ObjectiveScores(cost_usd=0.01, latency_ms=90, quality=0.95, compliance=1.0)),
        # 2. Wide blast -> NEEDS_HUMAN even with great scores
        ("rule.new",
         {"pattern": "auto-approve flight bookings under $500"},
         "wide",
         ObjectiveScores(cost_usd=0.01, latency_ms=90, quality=0.95, compliance=1.0)),
        # 3. Compliance below the 0.9 floor -> AUTO_REJECT
        ("skill.new",
         {"agent": "hotels", "skill": "book_hotel_without_review"},
         "narrow",
         ObjectiveScores(cost_usd=0.01, latency_ms=90, quality=0.95, compliance=0.4)),
    ]
    for kind, payload, blast, obj in proposals:
        p = gate.propose(kind, payload, blast_radius=blast, objectives=obj)
        decision, after = gate.auto_evaluate(p.id)
        print(f"  #{after.id} {after.kind:<14} blast={after.blast_radius:<7} "
              f"scalar={after.score:.3f} compliance={obj.compliance:.2f} "
              f"-> {decision.value.upper():<13} (status={after.status.value})")

    # (6) --- why() ---------------------------------------------------
    section("(6) WHY -- cross-source provenance for turn 4 (hotel booking)")
    if len(decision_ids) >= 4:
        joint = bot.why(decision_ids[3])
        print(f"  tracer chain events: {len(joint.get('tracer_chain', []))}")
        mnema = joint.get("mnema") or {}
        if isinstance(mnema, dict) and "error" not in mnema:
            print(f"  mnema keys:          {list(mnema.keys())}")
            # peek at what was actually recorded
            assertion = mnema.get("assertion") or {}
            if isinstance(assertion, dict):
                task = assertion.get("task") or assertion.get("payload", {}).get("task")
                if task:
                    print(f"  recorded task:       {str(task)[:120]}")

    # (7) --- memory recall proves cross-turn state -------------------
    section("(7) MEMORY -- every routed turn recorded, replayable by decision id")
    rep = memory.report()
    print(f"  mnema report: {rep}")
    print(f"  {len(decision_ids)} routing decisions -> each replayable via bot.why(id):")
    for i, did in enumerate(decision_ids, 1):
        joint = bot.why(did)
        chain = joint.get("tracer_chain", [])
        mnema = joint.get("mnema") or {}
        assertion = mnema.get("assertion") if isinstance(mnema, dict) else None
        chosen = "?"
        conf = 0.0
        if isinstance(assertion, dict):
            subject = assertion.get("subject") or ""
            parts = subject.split("::")
            if len(parts) >= 4 and parts[0] == "decision":
                chosen = f"{parts[2]}::{parts[3]}"
            conf = (assertion.get("confidence") or {}).get("value") or 0.0
        print(f"    turn {i}: {did[:20]}...  chain={len(chain)}  chosen={chosen:<20} conf={conf:.3f}")

    print("\n" + "=" * 76)
    print(" TRAVEL BOT COMPLETE.")
    print(" What you wrote: 4 tools + 2 agents + 1 mesh() call.")
    print(" What graxella did silently: routing, memory, peer-awareness,")
    print(" governance, gate scoring, provenance. Zero JSON schemas.")
    print("=" * 76)


if __name__ == "__main__":
    main()
