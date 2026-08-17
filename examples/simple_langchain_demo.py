"""The smallest possible Graxella example — plain LangChain, no LangGraph.

Two runs, side by side:

  1. Without Graxella: the LLM knows only the deprecated tool. It calls
     `get_weather`, the tool raises, the LLM sees an error. Classic drift.

  2. With Graxella: same LLM, same tool binding — but we've promoted one
     rulebook entry saying "get_weather -> fetch_forecast (rename city ->
     location)". Now the LLM's tool call is transparently rerouted; the
     LLM never sees the failure.

Requires: `ollama serve` running locally with `qwen2.5:3b` pulled.
    ollama pull qwen2.5:3b
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "graxella"))

from langchain_core.tools import tool
from langchain_ollama import ChatOllama

import graxella
from graxella import Proposal, Rulebook


# ---------- 1. Two tools: v1 (broken), v2 (works with a renamed arg) ----------
@tool
def get_weather(city: str) -> str:
    """Legacy weather API. Deprecated — always fails now."""
    raise RuntimeError("weather_v1 drift: `city` rejected by upstream")


@tool
def fetch_forecast(location: str) -> str:
    """Current weather API — takes `location`, not `city`."""
    return f"forecast for {location}: sunny, 27C"


TOOLS = [get_weather, fetch_forecast]


# ---------- 2. LLM bound ONLY to the legacy tool ----------
# Simulates the common failure mode: the agent was built against v1 and
# nobody has refreshed the tool bindings.
llm = ChatOllama(model="qwen2.5:3b", temperature=0)
llm_with_tools = llm.bind_tools([get_weather])


# The "dispatch" step is where routing decisions happen. Passing a different
# dispatcher is the only difference between the two runs below.
Dispatcher = Callable[[str, dict], str]


def call_llm(prompt: str, dispatch: Dispatcher) -> tuple[str, dict | None]:
    ai = llm_with_tools.invoke([
        ("system", "When asked about weather, call the get_weather tool."),
        ("human", prompt),
    ])
    if not ai.tool_calls:
        return (str(ai.content), None)
    tc = ai.tool_calls[0]
    try:
        return (str(dispatch(tc["name"], tc["args"])), None)
    except Exception as e:
        return ("", {"type": type(e).__name__, "msg": str(e)})


# ---------- 3. Baseline dispatch — no Graxella ----------
def raw_dispatch(name: str, args: dict) -> str:
    return {t.name: t for t in TOOLS}[name].invoke(args)


print("=== Without Graxella ===")
result, err = call_llm("What's the weather in Bengaluru?", raw_dispatch)
print(f"  result: {result!r}")
print(f"  error:  {err}")


# ---------- 4. One line of setup — promote a rule to the Rulebook ----------
# In real life the Proposal comes from mining docs (Phase 4) or lived
# episodes (Phase 3) or Datalog derivation (Phase 7). Here we hand-craft
# it to keep the example short.
here = Path(__file__).parent
rulebook_path = here / "rulebook_simple.json"
if rulebook_path.exists():
    rulebook_path.unlink()

rulebook = Rulebook(path=rulebook_path)
rulebook.promote(
    Proposal(
        id="prop_manual_1",
        kind="rule",
        subject="weather:get_weather->fetch_forecast",
        change={
            "if_intent": "weather",
            "replace_skill": "get_weather",
            "with_skill": "fetch_forecast",
            "recipe": {"field_map": {"city": "location"}},
        },
        evidence="hand-crafted for this demo",
        derived_from=["demo"],
        confidence=1.0,
    ),
    approved_by="me",
)

# ---------- 5. Graxella dispatch — one function call ----------
# wrap_tools returns {name: routed_callable}. Each callable consults the
# rulebook first; if a substitution is approved, it applies the field_map
# and calls the successor tool. Otherwise it calls the original.
routed = graxella.wrap_tools(TOOLS, rulebook=rulebook, intent="weather")

def graxella_dispatch(name: str, args: dict) -> str:
    return routed[name](args)


print("\n=== With Graxella ===")
result, err = call_llm("What's the weather in Bengaluru?", graxella_dispatch)
print(f"  result: {result!r}")
print(f"  error:  {err}")


# ---------- 6. Show what's in the rulebook ----------
print("\n=== Rulebook state ===")
for r in rulebook.all_rules():
    print(f"  {r.replace_skill} -> {r.with_skill}  recipe={r.recipe}")

print("\ndone. Same LLM, same bindings, same prompt — one promoted rule "
      "flipped the behavior from broken to working, with zero LLM retry.")
