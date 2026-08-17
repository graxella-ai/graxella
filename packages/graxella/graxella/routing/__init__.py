"""graxella.routing — deterministic destination picker (B11 agent2society).

For dispatcher nodes that need to choose one specialist tool / agent /
subgraph per query. Two backends: `lite` (token overlap, zero deps) and
`b11` (opt-in TF-IDF router).

Public surface:
    Router, Destination
    router               — build a Router from bare dicts or Destination objs
    route_by_query       — a LangGraph node factory
"""
from graxella.routing.router import (Destination, Router, route_by_query,
                                     router)

__all__ = ["Destination", "Router", "route_by_query", "router"]
