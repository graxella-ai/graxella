"""Semantic tool catalog.

`catalog(tools).find(query)` returns a LangChain BaseTool list re-ranked by
semantic relevance to the query. Two backends:

  * `lite`  (default) — pure-Python token-overlap ranker, zero heavy deps.
                        Deterministic, ~5μs per candidate, fine for catalogs
                        under a few hundred tools.
  * `b8`              — full B8 KnowledgeGraph (sentence-transformers + FAISS
                        + NetworkX shared-tag edges). Optional heavy import;
                        raises with a clear message if not installed.

Rationale: the router shouldn't need an LLM at inference time (compile-time
LLM > runtime LLM). Lite is the default because 90% of enterprise catalogs
are small; b8 kicks in when the customer has thousands of tools and needs
semantic recall over descriptions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Literal

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool  # noqa: F401

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


@dataclass
class _Scored:
    tool: "BaseTool"
    score: float


class ToolCatalog:
    """Ranked view over a set of LangChain BaseTools.

    Keep the interface tiny — LangGraph users just want "give me the top-k
    tools for this query" and "give me all of them for LangGraph.add_node."
    """

    def __init__(self, tools: Iterable["BaseTool"], *,
                 backend: Literal["lite", "b8"] = "lite") -> None:
        self._tools = list(tools)
        self._backend = backend
        self._b8 = _make_b8(self._tools) if backend == "b8" else None

    def all(self) -> list["BaseTool"]:
        return list(self._tools)

    def find(self, query: str, k: int = 4) -> list["BaseTool"]:
        if self._b8 is not None:
            names = [name for name, _score in self._b8.find_relevant_tools(query, top_k=k)]
            by_name = {t.name: t for t in self._tools}
            return [by_name[n] for n in names if n in by_name]

        q = _tokens(query)
        if not q:
            return self._tools[:k]
        scored = [
            _Scored(t, _lite_score(q, t))
            for t in self._tools
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        return [s.tool for s in scored[:k] if s.score > 0.0]

    def as_langchain(self) -> list["BaseTool"]:
        """LangGraph often wants the full list to bind into a ToolNode."""
        return list(self._tools)


def _lite_score(query_tokens: set[str], tool: "BaseTool") -> float:
    """Token-overlap / query-size — cheap, deterministic, symmetric-agnostic."""
    doc = f"{tool.name} {tool.description or ''}"
    doc_tokens = _tokens(doc)
    if not query_tokens:
        return 0.0
    hits = len(query_tokens & doc_tokens)
    # Small bonus if a query token appears in the tool NAME (stronger signal
    # than description-only match).
    name_tokens = _tokens(tool.name)
    hits += 0.5 * len(query_tokens & name_tokens)
    return hits / len(query_tokens)


def catalog(tools: Iterable["BaseTool"], *,
            backend: Literal["lite", "b8"] = "lite") -> ToolCatalog:
    """Build a ranked tool catalog. Default backend is `lite` (no heavy deps)."""
    return ToolCatalog(tools, backend=backend)


# ---------------------------------------------------------------- B8 binding
def _make_b8(tools: list["BaseTool"]):
    """Wire the B8 KnowledgeGraph over a synthetic ToolRegistry.

    The trick: B8 was built for its own tool objects (with `.description`
    and `.tags`), but we only receive LangChain BaseTools. We build a
    shim registry so B8 can index them without changes.
    """
    try:
        import sys
        from pathlib import Path
        b8_root = Path(__file__).resolve().parents[3] / "B8_Enterprise_MCP_Orchestration"
        if str(b8_root) not in sys.path:
            sys.path.insert(0, str(b8_root))
        from axon.graph.knowledge_graph import KnowledgeGraph  # noqa
    except Exception as e:  # pragma: no cover
        raise ImportError(
            "backend='b8' requires B8_Enterprise_MCP_Orchestration on sys.path "
            "and sentence-transformers + faiss installed. "
            f"Original error: {e}"
        ) from e

    class _Shim:
        def __init__(self, tool: "BaseTool"):
            self.name = tool.name
            self.description = tool.description or ""
            self.tags: list[str] = []

    class _ShimRegistry:
        def __init__(self, items: list[_Shim]):
            self._items = items

        def list_tools(self):
            return self._items

    shims = [_Shim(t) for t in tools]
    kg = KnowledgeGraph(_ShimRegistry(shims))
    kg.build()
    return kg
