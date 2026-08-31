"""Regression: ordinary paraphrases must route under the default router.

External probe (2026-08-30): with the old lexical default, the most
natural refund request imaginable -- "my package never showed up and I
want my money back" -- raised NoRouteError, and weather paraphrases
("should I carry an umbrella") returned None. The default is now
embedding-first (``router='auto'``). These tests run the semantic path
when a local embedding model is reachable and are SKIPPED otherwise
(the lexical fallback is exercised by the rest of the suite).

Also verifies P1-#5: an unroutable task raises a self-explaining error.
"""
from __future__ import annotations

import os

import pytest

import graxella
from graxella.mesh import _AUTO_EMBED_CACHE


def _ollama_up() -> bool:
    try:
        from mnema.adapters.embedder.ollama import OllamaEmbedder
        return OllamaEmbedder().health_check()
    except Exception:
        return False


semantic = pytest.mark.skipif(not _ollama_up(),
                              reason="needs a local Ollama embedder")


@pytest.fixture()
def auto_router(monkeypatch):
    """Lift the suite's lexical pin and reset the auto-probe cache."""
    monkeypatch.delenv("GRAXELLA_ROUTER", raising=False)
    _AUTO_EMBED_CACHE[0] = "unset"
    yield
    _AUTO_EMBED_CACHE[0] = "unset"


def _support_mesh(grx):
    def refunds(task: str) -> dict:
        """decide refunds and money-back requests for damaged or lost orders"""
        return {"result": "refund approved"}

    def shipping(task: str) -> dict:
        """track shipments and delivery status for orders"""
        return {"result": "tracking info"}

    return grx.mesh([refunds, shipping])


@semantic
def test_critics_probe_routes(auto_router):
    grx = graxella.Session("t", domain="support", workdir="ephemeral")
    app = _support_mesh(grx)
    result, _aid = app.route("my package never showed up and I want my money back")
    assert result.chosen_agent in ("refunds", "shipping")   # routed, not NoRouteError
    assert result.score > 0


@semantic
def test_paraphrases_route(auto_router):
    grx = graxella.Session("t2", domain="weather", workdir="ephemeral")

    def weather(task: str) -> dict:
        """current weather, temperature and rain forecast for a city"""
        return {"result": "sunny"}

    def stocks(task: str) -> dict:
        """stock prices and market data for a ticker"""
        return {"result": "up"}

    app = grx.mesh([weather, stocks])
    for phrasing in ["is it going to rain tomorrow",
                     "should I carry an umbrella today",
                     "how hot is it outside"]:
        result, _aid = app.route(phrasing)
        assert result.chosen_agent == "weather", (
            f"{phrasing!r} -> {result.chosen_agent}")


def test_noroute_error_is_self_explaining():
    """Even on the lexical path, an unroutable task must diagnose itself."""
    from agent2society import NoRouteError
    grx = graxella.Session("t3", domain="x", workdir="ephemeral")

    def alpha(task: str) -> dict:
        """alpha specialised work"""
        return {"result": "ok"}

    app = grx.mesh([alpha], router="tfidf")
    with pytest.raises(NoRouteError) as ei:
        app.route("completely unrelated request about zebras")
    msg = str(ei.value)
    assert "Fix" in msg or "Fixes" in msg
    assert "sentence-transformers" in msg or "nomic-embed-text" in msg
