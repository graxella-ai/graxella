"""Ten fake 'assistant' skills with realistic, verbose descriptions.

Descriptions are deliberately verbose because that is what a real skill
registry looks like — SKILL.md files, tool docstrings, MCP schemas — all
tend to be a paragraph or two per capability. That verbosity is exactly
what makes flat binding expensive: 10 verbose skills = 1500+ tokens in
every prompt whether the query needs them or not.

Bodies return deterministic strings so the demo is reproducible without
any real external services.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
def get_weather(city: str, date: str = "today") -> str:
    """Get the current weather forecast for a city.

    Returns temperature, sky conditions, humidity, and wind for the
    requested date. Supports 'today', 'tomorrow', or a YYYY-MM-DD date
    within the next 7 days. Data is sourced from the OpenWeather API
    and refreshed every 15 minutes. Coverage is worldwide but resolution
    is limited to municipality centroids for smaller towns.
    """
    return f"Weather in {city} on {date}: 18C, partly cloudy, humidity 62%, wind 8km/h NW"


@tool
def book_flight(origin: str, destination: str, date: str) -> str:
    """Search and price direct commercial flights between two cities.

    Returns up to three itinerary options sorted by price with times,
    airline, and cabin class. Origin and destination should be city
    names or IATA codes (LHR, CDG, etc.). Date is YYYY-MM-DD. Only
    direct flights are returned — for connections use search_flights_multi.
    Prices are indicative and refresh every 30 minutes.
    """
    return (f"Flights {origin} -> {destination} on {date}:\n"
            f"  BA234  09:00-11:30  $285\n"
            f"  AF678  14:20-16:50  $310\n"
            f"  LH901  19:00-21:30  $265")


@tool
def find_hotel(city: str, checkin: str, checkout: str) -> str:
    """Search hotels in a city between check-in and check-out dates.

    Returns three accommodation options with star rating, nightly rate,
    and aggregated review score from Booking.com. Dates are YYYY-MM-DD.
    Results are ranked by a blend of price and review score. For serviced
    apartments or short-term rentals see find_rental (not in this catalog).
    """
    return (f"Hotels in {city} ({checkin} -> {checkout}):\n"
            f"  Hotel Central       4* $180/night  8.7/10\n"
            f"  The Grand Boutique  5* $310/night  9.1/10\n"
            f"  Cozy Inn            3* $95/night   8.2/10")


@tool
def search_restaurants(city: str, cuisine: str) -> str:
    """Find restaurants in a city filtered by cuisine type.

    Returns three restaurants ranked by aggregated review score.
    Cuisine can be a broad category ('italian', 'japanese', 'vegetarian')
    or a specific dish ('ramen', 'paella'). Data is sourced from Google
    Maps and TripAdvisor and includes price band ($ to $$$$) and average
    review score.
    """
    return (f"{cuisine.title()} restaurants in {city}:\n"
            f"  Osteria Mario      $$   4.7/5\n"
            f"  Trattoria Della Nonna $$$ 4.6/5\n"
            f"  Pizzeria Fratelli  $    4.5/5")


@tool
def get_local_time(city: str) -> str:
    """Get the current local time and timezone for a city.

    Returns the local wall-clock time, timezone abbreviation, UTC offset,
    and daylight savings status. Useful for scheduling calls or knowing
    when local businesses are open. Handles historical timezone changes
    and DST transitions correctly.
    """
    return f"Local time in {city}: 14:32 JST (UTC+9), no DST"


@tool
def convert_currency(amount: float, from_currency: str, to_currency: str) -> str:
    """Convert an amount of money between two currencies at current rates.

    Uses mid-market rates from XE.com refreshed every 60 seconds. Both
    currencies should be three-letter ISO codes (USD, EUR, JPY, GBP, etc.).
    Returns the converted amount, the exchange rate used, and a note
    about spread if the two currencies are exotic. Historical rates are
    not supported — use fx_historical for that.
    """
    return f"{amount} {from_currency} = 79500 {to_currency} (rate 1 {from_currency} = 159 {to_currency})"


@tool
def check_visa(from_country: str, to_country: str) -> str:
    """Check visa requirements for travel between two countries.

    Returns whether a visa is required for a passport holder of
    from_country entering to_country, the visa type (tourist, business,
    e-visa on arrival, etc.), typical validity period, and processing
    time. Data is sourced from IATA Timatic and refreshed daily.
    """
    return (f"{from_country} passport to {to_country}: "
            f"Visa waiver, 90 days, no advance application needed")


@tool
def calculate_expense(category: str, amount: float, currency: str = "USD") -> str:
    """Log a business expense to the connected expense report.

    Category should be one of: 'meals', 'lodging', 'transport', 'other'.
    Amount is a positive number. Currency defaults to USD. Returns the
    expense id and running total for the current report. Expenses are
    marked pending and must be reviewed in the expense portal before
    submission.
    """
    return f"Logged {category} expense: {amount} {currency} (id EXP-4471, running total 340 USD)"


@tool
def translate_phrase(text: str, target_language: str) -> str:
    """Translate a short phrase from English into a target language.

    Target language should be a language name ('japanese') or ISO code
    ('ja'). Returns the translated text plus a phonetic transliteration
    for non-Latin scripts. Backed by DeepL for European languages and
    Google Translate for Asian scripts. Best for phrases under 200 chars.
    """
    return f"'{text}' in {target_language}: ありがとう (arigatou)"


@tool
def get_traffic(city: str, route_description: str) -> str:
    """Get real-time traffic conditions and estimated travel time on a route.

    Route description is a free-text like 'downtown to airport' or
    'Shibuya station to Tokyo Tower'. Returns current travel time,
    normal (off-peak) travel time, delay in minutes, and any active
    incidents on the route. Data is sourced from Google Maps and
    refreshed every 5 minutes.
    """
    return (f"Traffic in {city} on route '{route_description}': "
            f"25min now vs 18min normal (+7min delay), no incidents")


SKILLS = [
    get_weather, book_flight, find_hotel, search_restaurants, get_local_time,
    convert_currency, check_visa, calculate_expense, translate_phrase, get_traffic,
]
SKILLS_BY_NAME = {s.name: s for s in SKILLS}
