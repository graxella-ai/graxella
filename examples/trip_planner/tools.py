"""Fake travel-API tools for the trip-planner demo.

Realism knobs:
  * `search_hotels_v1` — the API the LLM was trained on. Sunset in Q2 2026;
    it always raises. Accepts `checkin`/`checkout` in its signature (v1 API
    had them too) but ignores them — the drift is not the schema, it is that
    the endpoint itself returns errors regardless of args.
  * `find_accommodations` — the current successor. Renamed `location` -> `city`.
  * The other three tools work — flights, activities, weather — so the
    itinerary composer has real content to weave.

Kept import-light: no dependency on graxella. Both the pure-LangGraph and the
Graxella notebooks import from this exact module.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
def search_flights(origin: str, destination: str, date: str) -> str:
    """Search direct flights from origin to destination on a given date (YYYY-MM-DD).

    Returns 3 mocked options with times and prices.
    """
    return (
        f"Flights {origin} -> {destination} on {date}:\n"
        f"  * BA1234  09:00-11:30  $285 economy\n"
        f"  * AF5678  14:20-16:50  $310 economy\n"
        f"  * LH9012  19:00-21:30  $265 economy"
    )


@tool
def search_hotels_v1(location: str, checkin: str = "", checkout: str = "") -> str:
    """Legacy hotels API (v1). DEPRECATED Q2 2026 — always raises.

    Successor: `find_accommodations(city, checkin, checkout)` — same three
    args, just `location` renamed to `city`. Docs on the migration are in
    `sample_docs/hotels_migration.md`.
    """
    raise RuntimeError(
        "hotels_v1 sunset: endpoint returns 410 Gone. "
        "Use find_accommodations(city, checkin, checkout) instead."
    )


@tool
def find_accommodations(city: str, checkin: str, checkout: str) -> str:
    """Search hotels in `city` between `checkin` and `checkout` (YYYY-MM-DD).

    Returns 3 mocked options with star rating and nightly price.
    """
    return (
        f"Hotels in {city} ({checkin} -> {checkout}):\n"
        f"  * Hotel Central       4*  $180/night   Booking.com 8.7\n"
        f"  * The Grand Boutique  5*  $310/night   Booking.com 9.1\n"
        f"  * Cozy Inn            3*  $95/night    Booking.com 8.2"
    )


@tool
def search_activities(city: str, interests: str) -> str:
    """Return 3 activity ideas in `city` matching the `interests` string."""
    return (
        f"Activities in {city} for interests '{interests}':\n"
        f"  * Guided walking tour of the old town (2h, $25)\n"
        f"  * Regional cuisine cooking class (3h, $65)\n"
        f"  * Museum of local history (self-guided, $12)"
    )


TOOLS = [search_flights, search_hotels_v1, find_accommodations, search_activities]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
