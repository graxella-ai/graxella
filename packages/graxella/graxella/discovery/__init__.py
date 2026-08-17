"""graxella.discovery — semantic tool catalog (B8 AXON territory).

Given a set of LangChain BaseTools, `catalog(tools).find(query)` returns
the top-k tools by semantic relevance. Two backends:

  * 'lite'  (default) — pure-Python token overlap, zero heavy deps.
  * 'b8'              — full B8 KnowledgeGraph (sentence-transformers +
                        FAISS + NetworkX). Opt-in heavy import.

Public surface:
    ToolCatalog, catalog
"""
from graxella.discovery.catalog import ToolCatalog, catalog

__all__ = ["ToolCatalog", "catalog"]
