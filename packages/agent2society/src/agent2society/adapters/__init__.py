"""Framework adapters.

Each adapter wraps a native agent (CrewAI crew, LangGraph graph, AutoGen
group chat, etc.) into a pair of:
  * an A2A AgentCard (so the capability graph can route to it)
  * a local handler the dispatcher can invoke in-process

To register your own adapter:

    from agent2society.adapters.base import Adapter, register_adapter

    class MyFwAdapter(Adapter):
        def matches(self, obj): ...
        def to_card(self, obj): ...
        def to_handler(self, obj): ...

    register_adapter(MyFwAdapter())
"""
from __future__ import annotations

from .base import (
    Adapter,
    AdaptedAgent,
    adapt,
    register_adapter,
    registered_adapters,
)
from .callable import CallableAdapter
from .crewai import CrewAIAdapter
from .langgraph import LangGraphAdapter
from .autogen import AutogenAdapter

# Default registration order matters: more specific adapters first,
# generic callable last.
register_adapter(CrewAIAdapter())
register_adapter(LangGraphAdapter())
register_adapter(AutogenAdapter())
register_adapter(CallableAdapter())

__all__ = [
    "Adapter",
    "AdaptedAgent",
    "adapt",
    "register_adapter",
    "registered_adapters",
    "CallableAdapter",
    "CrewAIAdapter",
    "LangGraphAdapter",
    "AutogenAdapter",
]
