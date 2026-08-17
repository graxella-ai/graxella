"""Router LangGraph node — deterministic destination picker.

Pattern: many LangGraph apps have a single dispatcher node that decides
which specialist tool/agent handles the current query. Today devs write
this by hand — a chain of `if/elif` on state["intent"] or a prompt-driven
LLM classifier. Both age badly: the if-chain forgets tools, the LLM burns
tokens.

This module supplies a factory that returns a node function usable with
`add_node` + `add_conditional_edges`. The node reads a query field from
state, ranks the registered destinations by TF-IDF-lite overlap, and
writes the top pick into a chosen state field. LangGraph's conditional
edge then dispatches on that field.

Two backends:

  * 'lite' (default) — token overlap, zero heavy deps, sub-millisecond.
  * 'b11'            — B11 agent2society TF-IDF router; opt-in heavy import.

Design principle: compile-time LLM > runtime LLM. The router itself never
calls an LLM; only human-authored descriptions + the incoming query
participate in the decision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig  # noqa: F401


_WORD = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


@dataclass
class Destination:
    """One routable target — a tool name, an agent uri, a subgraph label."""
    name: str
    description: str = ""
    tags: tuple[str, ...] = ()

    def _doc_tokens(self) -> set[str]:
        return _tokens(f"{self.name} {self.description} {' '.join(self.tags)}")


@dataclass
class Router:
    """Ranks Destinations by a query. `pick(query)` returns a name.

    Kept intentionally small: `pick`, `top_k`, `all_names`. Nothing else —
    the surface has to be trivial for a LangGraph node to embed.
    """
    destinations: list[Destination]
    backend: Literal["lite", "b11"] = "lite"
    _b11: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.backend == "b11":
            self._b11 = _make_b11(self.destinations)

    def all_names(self) -> list[str]:
        return [d.name for d in self.destinations]

    def top_k(self, query: str, k: int = 3) -> list[tuple[str, float]]:
        if self._b11 is not None:
            return self._b11(query, k)
        q = _tokens(query)
        if not q:
            return [(d.name, 0.0) for d in self.destinations[:k]]
        scored: list[tuple[str, float]] = []
        for d in self.destinations:
            hits = len(q & d._doc_tokens())
            if hits == 0:
                continue
            name_bonus = 0.5 * len(q & _tokens(d.name))
            scored.append((d.name, (hits + name_bonus) / len(q)))
        scored.sort(key=lambda s: s[1], reverse=True)
        return scored[:k]

    def pick(self, query: str, *, default: str | None = None) -> str | None:
        top = self.top_k(query, k=1)
        if top:
            return top[0][0]
        return default


def router(destinations: Iterable[Destination | dict[str, Any]],
           *,
           backend: Literal["lite", "b11"] = "lite") -> Router:
    """Build a Router. Accepts Destination objects OR bare dicts
    ({"name": ..., "description": ..., "tags": [...]})."""
    out: list[Destination] = []
    for d in destinations:
        if isinstance(d, Destination):
            out.append(d)
        else:
            out.append(Destination(
                name=d["name"],
                description=d.get("description", ""),
                tags=tuple(d.get("tags", ())),
            ))
    return Router(destinations=out, backend=backend)


def route_by_query(
    r: Router,
    *,
    query_key: str = "query",
    output_key: str = "__route__",
    default: str | None = None,
) -> Callable[..., dict[str, Any]]:
    """Return a LangGraph-compatible node function.

    Usage:
        r = router([
            {"name": "weather", "description": "current forecast, temperature, sky"},
            {"name": "market",  "description": "stock price, market data"},
        ])
        b.add_node("route", route_by_query(r))
        b.add_conditional_edges("route", lambda s: s["__route__"],
                                {"weather": "weather_node", "market": "market_node"})
    """
    def _node(state: dict[str, Any],
              config: "RunnableConfig | None" = None) -> dict[str, Any]:
        query = state.get(query_key, "")
        pick = r.pick(query, default=default)
        return {**state, output_key: pick}

    _node.__name__ = "graxella_router_node"
    return _node


# ---------------------------------------------------------------- B11 binding
def _make_b11(destinations: list[Destination]):
    """Wire B11's TF-IDF router if available. Shim wraps it in the same
    (query, k) -> [(name, score)] shape as our lite backend."""
    try:
        import sys
        from pathlib import Path
        b11_root = Path(__file__).resolve().parents[3] / "B11_agent2society"
        if str(b11_root) not in sys.path:
            sys.path.insert(0, str(b11_root))
        from agent2society.routing.tfidf_router import TfidfRouter  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ImportError(
            "backend='b11' requires B11_agent2society on sys.path. "
            f"Original error: {e}"
        ) from e

    corpus = [
        {"id": d.name,
         "text": f"{d.name} {d.description} {' '.join(d.tags)}"}
        for d in destinations
    ]
    tf = TfidfRouter(corpus=corpus)

    def _call(query: str, k: int) -> list[tuple[str, float]]:
        return [(hit["id"], hit["score"]) for hit in tf.rank(query, top_k=k)]

    return _call
