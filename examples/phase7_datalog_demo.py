"""Phase-7 demo — Datalog-derived rules + a real Ollama (qwen2.5:3b) agent.

The story this demo tells:

  1. Docs describe TWO single-hop deprecations:
        get_weather        -> fetch_forecast_v2
        fetch_forecast_v2  -> fetch_forecast_v3
     Neither doc says "get_weather is deprecated by fetch_forecast_v3".

  2. DocsMiner (Phase 4) only sees each hop in isolation, so it emits two
     Proposals — neither of them the transitive one.

  3. DatalogMiner (Phase 7) evaluates a small Datalog program over the same
     seed and DERIVES the transitive substitute(weather_lookup, get_weather,
     fetch_forecast_v3). That derived Proposal is the interesting one — it
     represents knowledge NO single document asserted.

  4. A reviewer promotes it to the Rulebook.

  5. `graxella.wrap()` wraps a small LangGraph agent driven by a real
     open-source LLM (qwen2.5:3b via Ollama). The LLM chooses to call
     `get_weather` — but the wrap layer's rulebook lookup detects the
     transitively-approved substitution and routes to `fetch_forecast_v3`
     without the LLM ever seeing the v1 failure.

  6. The Episode ledger and audit bundle confirm the bypass: only
     `fetch_forecast_v3=ok`, no v1 error, one closed audit graph.

Prereqs: Ollama running on localhost:11434 with qwen2.5:3b pulled.
Verified via `ollama list` before writing this demo.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
for _sub in ("graxella",):
    p = _REPO / _sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langchain_ollama import ChatOllama  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402
from langgraph.graph.message import MessagesState  # noqa: E402
from langgraph.prebuilt import ToolNode, tools_condition  # noqa: E402

import graxella  # noqa: E402
from graxella import Rulebook, SqliteExperienceStore  # noqa: E402
from graxella.audit import export as audit_export  # noqa: E402
from graxella.agenda import DatalogMiner, DocsMiner  # noqa: E402
from graxella.knowledge import from_docs  # noqa: E402


MODEL = "qwen2.5:3b"
INTENT = "weather_lookup"


# --- tools: v1 fails, v2 also fails (imagined mid-migration), v3 works ----
@tool
def get_weather(city: str) -> str:
    """v1 weather tool. Drift: rejects 'city' arg."""
    raise RuntimeError("weather_v1 drift: 'city' arg rejected by upstream")


@tool
def fetch_forecast_v2(location: str) -> str:
    """v2 weather tool. Also drifted — sunsetted before v3 rolled out."""
    raise RuntimeError("weather_v2 sunset: upstream returns 410 Gone")


@tool
def fetch_forecast_v3(location: str) -> str:
    """v3 weather tool — the current, working successor."""
    return f"[v3] forecast for {location}: sunny, 27C"


ALL_TOOLS = [get_weather, fetch_forecast_v2, fetch_forecast_v3]


# --- LangGraph app driven by a real OSS LLM -------------------------------
# The LLM is bound to ONLY the legacy `get_weather` tool — simulating the
# common failure mode where the agent's tool bindings are stale relative to
# the actual API surface. Graxella's wrap layer knows about all three; when
# the LLM emits a tool_call for get_weather, the rulebook (now containing
# the Datalog-derived transitive substitution) reroutes to fetch_forecast_v3
# without the LLM ever seeing v1 or v2.
def build_app():
    llm = ChatOllama(model=MODEL, temperature=0)
    llm_with_tools = llm.bind_tools([get_weather])   # stale bindings on purpose

    def llm_node(state: MessagesState) -> dict[str, Any]:
        resp = llm_with_tools.invoke(state["messages"])
        return {"messages": [resp]}

    # ToolNode needs to know every tool a tool_call name might resolve to.
    # Because wrap() has patched get_weather.invoke() to consult the rulebook
    # and dispatch to the approved successor, ToolNode's lookup of
    # `get_weather` returns v3's result.
    tool_node = ToolNode(ALL_TOOLS)

    b = StateGraph(MessagesState)
    b.add_node("llm", llm_node)
    b.add_node("tools", tool_node)
    b.set_entry_point("llm")
    b.add_conditional_edges("llm", tools_condition, {"tools": "tools", END: END})
    b.add_edge("tools", END)
    return b.compile()


# --- fixture docs (inlined so the demo is self-contained) -----------------
FIXTURE_DOC = """# Weather API — migration history

## get_weather

**Deprecated.** Superseded by `fetch_forecast_v2`.

## fetch_forecast_v2

**Deprecated.** Superseded by `fetch_forecast_v3`.

Migrates from `get_weather` to `fetch_forecast_v2`:

| Old  | New      |
| ---- | -------- |
| city | location |

## fetch_forecast_v3

### fetch_forecast_v3(location)

Fetches the current weather forecast for a location.

Intent: weather_lookup
"""


# --- run ------------------------------------------------------------------
def main() -> None:
    here = Path(__file__).parent
    docs_dir = here / "sample_docs_phase7"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "weather_chain.md").write_text(FIXTURE_DOC, encoding="utf-8")

    store_path = here / "experience_phase7.db"
    rulebook_path = here / "rulebook_phase7.json"
    audit_path = here / "audit_phase7.jsonld"
    for p in (store_path, rulebook_path, audit_path):
        if p.exists():
            p.unlink()

    # -- 7.1 — ingest --------------------------------------------------
    print(f"=== Ingesting docs ({docs_dir.name}) ===")
    seed = from_docs(docs_dir)
    print(f"  {len(seed.assertions)} assertions: {json.dumps(seed.stats())}")

    # -- 7.2 — DocsMiner ONLY sees single-hop -------------------------
    print("\n=== DocsMiner proposals (single-hop, one Assertion each) ===")
    docs_props = DocsMiner(seed=seed, default_intent=INTENT).mine([])
    for p in docs_props:
        print(f"  {p.subject}  conf={p.confidence:.2f}")

    # -- 7.3 — DatalogMiner derives the transitive fact ---------------
    print("\n=== DatalogMiner proposals (transitive derivations) ===")
    dl_props = DatalogMiner(seed=seed).mine([])
    for p in dl_props:
        marker = "  <-- TRANSITIVE" if "->" in p.evidence.split("chain ", 1)[-1] and \
                                        p.evidence.count("->") >= 2 else ""
        print(f"  {p.subject}  conf={p.confidence:.2f}{marker}")

    transitive = next((p for p in dl_props
                       if p.change.get("replace_skill") == "get_weather"
                       and p.change.get("with_skill") == "fetch_forecast_v3"),
                      None)
    assert transitive is not None, "expected a transitive get_weather->v3 proposal"
    print(f"\n  transitive proposal id: {transitive.id}")
    print(f"  evidence: {transitive.evidence}")
    print(f"  citations: {transitive.derived_from}")

    # -- 7.4 — reviewer promotes the transitive rule -------------------
    rulebook = Rulebook(path=rulebook_path)
    rule = rulebook.promote(transitive, approved_by="ram@graxella")
    print(f"\n=== Promoted {transitive.id} -> {rule.id} ===")
    print(f"  intent={rule.intent}  {rule.replace_skill} -> {rule.with_skill}")
    print(f"  recipe: {json.dumps(rule.recipe, default=str)}")

    # -- 7.5 — wrap a real Ollama-driven agent ------------------------
    print(f"\n=== Wrapping a LangGraph app driven by Ollama ({MODEL}) ===")
    store = SqliteExperienceStore(store_path)
    app = build_app()
    smart_app = graxella.wrap(
        app,
        tools=ALL_TOOLS,
        store=store,
        rulebook=rulebook_path,
        intent=INTENT,
    )

    print("\n=== Running the wrapped agent — LLM will call get_weather ===")
    system = SystemMessage(content=(
        "You are a helpful assistant. When the user asks about the weather in "
        "a city, ALWAYS call the `get_weather` tool with the city as the "
        "`city` argument. Do not attempt to answer from your own knowledge."
    ))
    user = HumanMessage(content="What's the weather in Bengaluru?")
    out = smart_app.invoke({"messages": [system, user]})

    # Pull the last ToolMessage (its `content` is what fetch_forecast_v3 returned).
    tool_msgs = [m for m in out["messages"] if getattr(m, "type", "") == "tool"]
    ai_msgs = [m for m in out["messages"]
               if getattr(m, "type", "") == "ai" and getattr(m, "tool_calls", None)]
    if ai_msgs:
        called = [tc["name"] for tc in ai_msgs[-1].tool_calls]
        print(f"  LLM chose to call: {called}")
    if tool_msgs:
        print(f"  Tool actually returned: {tool_msgs[-1].content!r}")

    # -- 7.6 — inspect the Episode ledger ----------------------------
    print("\n=== Episodes (expect fetch_forecast_v3=ok, no v1/v2 ERR) ===")
    for e in store.all():
        tcs = " | ".join(f"{tc.tool_id}={'ok' if tc.ok else 'ERR'}"
                         for tc in e.tool_calls) or "(no tools)"
        print(f"  {e.id[:14]}  session={e.session_id:8} tools=[{tcs}]")

    # -- 7.7 — audit closure -----------------------------------------
    print("\n=== PROV-O audit bundle ===")
    payload = audit_export(store, rulebook, docs_props + dl_props, path=audit_path)
    kinds: dict[str, int] = {}
    for node in payload.get("@graph", []):
        k = node.get("grx:kind", "?")
        kinds[k] = kinds.get(k, 0) + 1
    print(f"  wrote {audit_path.name}  nodes={len(payload['@graph'])}  breakdown={kinds}")

    store.close()
    print("\ndone. Datalog derived a substitution NO single doc asserted; the OSS "
          "LLM's tool call was transparently rerouted; the audit graph closes.")


if __name__ == "__main__":
    main()
