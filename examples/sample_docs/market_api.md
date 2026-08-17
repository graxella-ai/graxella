# Market Data API

Two tools cover the market data surface. `stock_price` remains supported
in read-only mode; the streaming replacement is `quote_stream` (out of scope
for the v0.1 catalog).

## stock_price(ticker: str) -> str

Returns the latest traded price of a stock ticker.

Intent: market_lookup

## news_headlines(topic: str) -> str

Returns recent news headlines about a topic.

Intent: news_lookup
