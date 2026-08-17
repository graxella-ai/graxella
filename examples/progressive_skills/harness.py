"""Shared harness — token counting, LLM invocation, and per-run metrics.

Two facts the demo hinges on:

1. TOKENS SENT TO THE LLM = system_prompt + serialized_tool_schemas + query.
   The tool-schema block is what a flat binding pays every request, whether
   or not the model needs those schemas. We measure it explicitly so the
   difference between 'all 10 tools bound' and 'router narrowed to top-3'
   is a hard number, not a vibe.

2. SELECTION ACCURACY = did the LLM call the correct subset of tools?
   Measured by comparing observed tool_calls against Task.expected_skills.

Ollama is the only real dependency. If it is not reachable we return
degraded metrics for the token count (which is deterministic) and mark
success=None. That way the demo still produces a useful comparison table
in air-gapped environments.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool


MODEL = "qwen2.5:3b"


# ---------------------------------------------------------------- token count
def _count_tokens(text: str) -> int:
    """Approximate token count (tiktoken-free).

    Uses the classic ~4 chars/token heuristic that matches OpenAI/Anthropic
    tokenizers within +/- 15% on English prose. Good enough for a comparison
    table where the ratio between two approaches is what matters, not the
    absolute number. If tiktoken is available we prefer it.
    """
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _tool_schema_str(tool: BaseTool) -> str:
    """Serialize a BaseTool the way an LLM tool binding would see it."""
    schema = {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.args if isinstance(tool.args, dict) else {},
    }
    return json.dumps(schema, ensure_ascii=False)


def prompt_tokens(system_prompt: str, tools: list[BaseTool], query: str) -> int:
    parts = [system_prompt] + [_tool_schema_str(t) for t in tools] + [query]
    return sum(_count_tokens(p) for p in parts)


# ---------------------------------------------------------------- Ollama check
def check_ollama() -> bool:
    try:
        from langchain_ollama import ChatOllama
        ChatOllama(model=MODEL, temperature=0, num_predict=1).invoke("hi")
        return True
    except Exception as e:
        print(f"[!] Ollama unreachable: {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------- run one task
SYSTEM_PROMPT = (
    "You are a helpful assistant. Choose the correct tool(s) from the "
    "available list to answer the user. Call every tool that is needed. "
    "Do not answer without calling tools if a tool applies."
)


@dataclass
class RunResult:
    query: str
    expected: frozenset[str]
    called: frozenset[str]
    tokens_prompt: int
    tokens_completion: int
    wall_s: float
    tools_exposed: int
    success: bool | None
    notes: list[str] = field(default_factory=list)


def invoke_with_tools(query: str, tools: list[BaseTool],
                      expected: frozenset[str]) -> RunResult:
    """Invoke qwen2.5:3b with a specific subset of tools bound.

    Returns a RunResult with prompt tokens (always), plus completion tokens,
    tool calls, wall time, and success (if Ollama is available).
    """
    tokens_p = prompt_tokens(SYSTEM_PROMPT, tools, query)
    notes: list[str] = []

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_ollama import ChatOllama
    except Exception as e:
        notes.append(f"langchain not importable: {e}")
        return RunResult(query=query, expected=expected, called=frozenset(),
                         tokens_prompt=tokens_p, tokens_completion=0,
                         wall_s=0.0, tools_exposed=len(tools),
                         success=None, notes=notes)

    llm = ChatOllama(model=MODEL, temperature=0).bind_tools(tools)
    msgs = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=query)]
    t0 = time.time()
    try:
        resp = llm.invoke(msgs)
    except Exception as e:
        notes.append(f"LLM invocation failed: {type(e).__name__}: {e}")
        return RunResult(query=query, expected=expected, called=frozenset(),
                         tokens_prompt=tokens_p, tokens_completion=0,
                         wall_s=round(time.time() - t0, 3),
                         tools_exposed=len(tools), success=None, notes=notes)
    wall = round(time.time() - t0, 3)

    called: set[str] = set()
    tcs = getattr(resp, "tool_calls", None) or []
    for tc in tcs:
        name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
        if name:
            called.add(name)

    body = resp.content if isinstance(resp.content, str) else str(resp.content)
    tokens_c = _count_tokens(body) + sum(
        _count_tokens(json.dumps(tc, default=str)) for tc in tcs
    )

    success = called == expected
    if not success:
        missing = expected - called
        extra = called - expected
        if missing:
            notes.append(f"missing: {sorted(missing)}")
        if extra:
            notes.append(f"extra: {sorted(extra)}")

    return RunResult(query=query, expected=expected, called=frozenset(called),
                     tokens_prompt=tokens_p, tokens_completion=tokens_c,
                     wall_s=wall, tools_exposed=len(tools),
                     success=success, notes=notes)


# ---------------------------------------------------------------- pricing
# Rough 2026 prices per million tokens. Adjust for whatever quote you have.
FRONTIER_INPUT_PER_M = 3.00    # e.g. Claude Sonnet 4.6
FRONTIER_OUTPUT_PER_M = 15.00
OSS_INPUT_PER_M = 0.02         # self-hosted qwen2.5:3b, compute only
OSS_OUTPUT_PER_M = 0.05


def cost_usd(tokens_in: int, tokens_out: int, *, frontier: bool) -> float:
    if frontier:
        return (tokens_in * FRONTIER_INPUT_PER_M
                + tokens_out * FRONTIER_OUTPUT_PER_M) / 1_000_000
    return (tokens_in * OSS_INPUT_PER_M
            + tokens_out * OSS_OUTPUT_PER_M) / 1_000_000
