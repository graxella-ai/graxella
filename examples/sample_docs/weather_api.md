# Weather API — v2 migration guide

This document describes the v2 weather tools that replace the v1 surface
originally shipped in 2024. All v1 tools are deprecated and will be removed
in 2026-Q4.

## get_weather(city: str) -> str

Fetches a current weather report for the given city name.

**Deprecated.** Use `fetch_forecast` instead. The legacy tool rejects most
non-ASCII city names since Q2.

## fetch_forecast(location: str) -> str

Fetches the current weather forecast (temperature, sky) for a location.

Intent: weather_lookup

Migrates from `get_weather`. The field rename table below documents the
argument-level differences a client must apply.

| Old | New |
| --- | --- |
| city | location |

Renamed `city` -> `location` in v2 to align with the internal geocoder
namespace.
