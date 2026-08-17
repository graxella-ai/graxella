"""Minimal ReAct loop — the honest way to reproduce the retry-storm.

`llm.invoke([messages])` on its own does not retry. What creates a retry
storm is the agent loop pattern: the model sees a ToolMessage containing
an error, decides to try again, calls the same tool. Without a cap, that
loop runs until the max_steps guard fires — and every step is a full LLM
call plus a tool call plus more tokens in the context window.

This module reproduces that pattern in ~40 lines so we can measure it
faithfully: same tools, same query, same qwen2.5:3b, guarded vs unguarded.

Returns a RunTrace with everything the compare script needs: which tools
were called, whether each succeeded, wall time per step, total tokens,
and a hard stop reason (finished / max_steps / stalled).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import (AIMessage, HumanMessage, SystemMessage,
                                     ToolMessage)
from langchain_core.tools import BaseTool


REACT_SYSTEM = (
    "You are a helpful assistant with tools. Call a tool if you need one. "
    "If a tool returns an error, do not immediately retry the same call — "
    "read the error and either try a different tool or stop and answer "
    "with what you have. Keep answers short."
)


@dataclass
class StepRecord:
    step: int
    tool_calls: list[str]
    tool_results: list[str]
    wall_s: float


@dataclass
class RunTrace:
    query: str
    steps: list[StepRecord] = field(default_factory=list)
    stop_reason: str = ""
    wall_s: float = 0.0
    final_text: str = ""
    total_llm_calls: int = 0

    def tools_called_flat(self) -> list[str]:
        out: list[str] = []
        for s in self.steps:
            out.extend(s.tool_calls)
        return out

    def unique_tools_called(self) -> set[str]:
        return set(self.tools_called_flat())


def react_run(llm_factory,
              tools: list[BaseTool],
              query: str,
              *,
              max_steps: int = 6) -> RunTrace:
    """Run a ReAct loop against a fresh LLM binding of `tools`.

    llm_factory is a zero-arg callable returning a Chat model so we can
    rebind tools on every run without carrying state across scenarios.
    max_steps is the hard stop that keeps a busted run from consuming
    infinite time — set it well above what a healthy run needs so any
    hits on it are real retry-storm signals, not sample size.
    """
    llm = llm_factory().bind_tools(tools)
    by_name = {t.name: t for t in tools}
    trace = RunTrace(query=query)

    msgs: list[Any] = [SystemMessage(content=REACT_SYSTEM),
                       HumanMessage(content=query)]
    t_run = time.time()

    for step_i in range(1, max_steps + 1):
        step_t0 = time.time()
        resp = llm.invoke(msgs)
        trace.total_llm_calls += 1
        msgs.append(resp)

        tcs = getattr(resp, "tool_calls", None) or []
        if not tcs:
            trace.stop_reason = "finished"
            trace.final_text = resp.content if isinstance(resp.content, str) \
                else str(resp.content)
            trace.steps.append(StepRecord(step=step_i,
                                          tool_calls=[],
                                          tool_results=[],
                                          wall_s=round(time.time() - step_t0, 3)))
            break

        names: list[str] = []
        results: list[str] = []
        for tc in tcs:
            name = tc["name"] if isinstance(tc, dict) else tc.name
            args = tc["args"] if isinstance(tc, dict) else tc.args
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            names.append(name)
            tool = by_name.get(name)
            if tool is None:
                content = f"unknown tool: {name}"
            else:
                try:
                    val = tool.invoke(args)
                except BaseException as e:  # noqa: BLE001
                    val = f"ERROR: {type(e).__name__}: {e}"
                content = val if isinstance(val, str) else json.dumps(val, default=str)
            results.append(content[:120])
            msgs.append(ToolMessage(content=content, tool_call_id=tc_id or ""))

        trace.steps.append(StepRecord(step=step_i,
                                      tool_calls=names,
                                      tool_results=results,
                                      wall_s=round(time.time() - step_t0, 3)))
    else:
        trace.stop_reason = "max_steps"

    trace.wall_s = round(time.time() - t_run, 3)
    return trace
