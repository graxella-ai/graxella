# Weather API — migration history

## get_weather

**Deprecated.** Superseded by `fetch_forecast_v2`.

## fetch_forecast_v2

**Deprecated.** Superseded by `fetch_forecast_v3`.

Migrates from `get_weather` to `fetch_forecast_v2`:

| Old  | New      |
| ---- | -------- |
| city | location |

## fetch_forecast_v3

### fetch_forecast_v3(location)

Fetches the current weather forecast for a location.

Intent: weather_lookup
