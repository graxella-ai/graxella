"""graxella.Agent -- CrewAI/LangChain-shaped agent constructor.

Optional convenience. Developers pick their path:

  * Path A -- native framework agent:
        triage = create_react_agent(llm, tools, name="triage")
        app = graxella.mesh([triage, ...], memory=...)

  * Path B -- graxella.Agent (this file):
        triage = graxella.Agent(
            role="Refund Triage Officer",
            goal="Decide if a refund should be issued for a complaint",
            backstory="You are a senior support agent.",
            tools=[check_order, lookup_policy],
            llm=ChatOllama(model="qwen2.5:3b"),
        )
        app = graxella.mesh([triage, ...], memory=...)

Both paths land in the SAME ``graxella.mesh(...)`` and are governed
identically. ``Agent`` is a thin dataclass that maps CrewAI's field
names onto graxella's primitives:

  * ``role``      -> routing name (slugified)
  * ``goal``      -> primary skill tag (used for TF-IDF routing)
  * ``backstory`` -> injected as system context on every LLM call
  * ``tools``     -> the callables the LLM may invoke (bound via LangGraph
                     ``create_react_agent`` when ``llm`` is provided)
  * ``llm``       -> optional. If absent, ``tools[0]`` is called directly
                     with the payload -- makes stub/mock agents trivial.

Real ``crewai.Agent`` objects share the same field names, so passing a
crewai agent into ``graxella.mesh([...])`` duck-types cleanly with no
adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Agent:
    role: str
    goal: str = ""
    backstory: str = ""
    tools: list[Any] = field(default_factory=list)
    llm: Any = None

    # -- internals used by Society.add() ------------------------------------

    def _routing_name(self) -> str:
        """Slugify role for use as the mesh agent id."""
        return "".join(ch if ch.isalnum() else "_" for ch in self.role).strip("_").lower() or "agent"

    def _skill_tags(self) -> list[str]:
        """Derive TF-IDF-friendly skill strings from goal + tools.

        Order: goal first (primary intent), then per-tool descriptions,
        then a fallback to the role itself if everything else is empty.
        """
        tags: list[str] = []
        if self.goal:
            tags.append(self.goal)
        for t in self.tools:
            desc = getattr(t, "description", None)
            name = getattr(t, "name", None) or getattr(t, "__name__", None)
            if desc:
                tags.append(desc.strip().split("\n")[0])
            elif name:
                tags.append(name.replace("_", " "))
        if not tags:
            tags.append(self.role)
        return tags

    def _make_runner(self, peer_context: str = "",
                     context_slot: list | None = None) -> Callable[[Any], Any]:
        """Return the callable Society will invoke when this agent is picked.

        Priority:
          1. LLM + tools present -> LangGraph ``create_react_agent`` loop.
             The LLM picks which tool to call, executes it, may loop, and
             returns a final message.
          2. Tools only (no LLM) -> call the first tool with the payload.
             Useful for stub agents / deterministic showcases.
          3. Neither -> echo. The agent is unusable but registers cleanly.

        ``peer_context`` is prepended as a system message when set (used
        by ``graxella.mesh(...)`` so this agent sees the peer directory).
        """
        if self.llm is not None and self.tools:
            try:
                from langgraph.prebuilt import create_react_agent  # type: ignore
            except ImportError as exc:
                raise ImportError(
                    "Agent(llm=..., tools=[...]) requires langgraph. "
                    "Install with: pip install -r requirements-llm.txt"
                ) from exc

            graph = create_react_agent(self.llm, self.tools)
            backstory = self.backstory

            def runner(payload: Any) -> dict:
                task = str(payload)
                msgs: list[Any] = []
                if peer_context:
                    msgs.append(("system", peer_context))
                if backstory:
                    msgs.append(("system", backstory))
                if context_slot and context_slot[0]:  # case recall (0B-2)
                    msgs.append(("system", context_slot[0]))
                msgs.append(("user", task))
                result = graph.invoke({"messages": msgs})
                messages = result.get("messages", [])
                final = messages[-1] if messages else None
                content = getattr(final, "content", "") if final else ""
                tool_calls = []
                for m in messages:
                    for tc in (getattr(m, "tool_calls", None) or []):
                        tool_calls.append({
                            "name": tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None),
                            "args": tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None),
                        })
                return {"result": content, "tool_calls": tool_calls}

            return runner

        if self.tools:
            first = self.tools[0]
            if hasattr(first, "invoke"):  # langchain BaseTool
                return lambda payload: {"result": first.invoke(payload)}
            return first  # plain callable

        return lambda payload: {"result": f"{self.role} received: {payload}"}
