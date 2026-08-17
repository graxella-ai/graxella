"""Phase-4 demo — intelligence BEFORE traffic.

The Phase-1-through-3 flow needed real users to hit real tools before any
substitution could be learned. Phase 4 closes that gap by mining what humans
already wrote down.

The story this script tells:

  1. `graxella.ingest.from_docs(...)` reads a fake docs directory and returns
     a KnowledgeSeed of typed Assertions:
        deprecated_by, field_maps_to, has_intent, has_arg, described_as

  2. `DocsMiner(seed).mine([])` emits Proposals — same shape RuleDistiller
     emits from lived Experience. Zero episodes required.

  3. Reviewer promotes one into the Phase-3 Rulebook.

  4. A LangGraph app dispatches the very first weather query via
     `heal.route(rulebook=...)`. Because the rulebook already carries the
     documented substitution + field_map, the legacy tool is NEVER invoked.
     No failure. No retry. No LLM at runtime.

  5. Combine: run the SAME docs miner alongside RuleDistiller against a
     store that has BOTH docs-only rules AND lived failures — the two
     evidence sources fuse into stronger proposals.

Zero heavy deps. Same audit surface (PROV-O) still applies — a Phase-3
`agenda audit` on this run will cite BOTH doc source_uris AND episode ids.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypedDict

_REPO = Path(__file__).resolve().parents[2]
for _sub in ("graxella",):
    p = _REPO / _sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from langchain_core.runnables import RunnableConfig  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402

from graxella import (Rulebook, SqliteExperienceStore, from_docs)  # noqa: E402
from graxella.agenda import DocsMiner  # noqa: E402
from graxella.healing import recipes as heal  # noqa: E402
from graxella.integrations.langgraph import GraxellaCallback  # noqa: E402


# --- tools (same shape as Phase 3) ---------------------------------------
@tool
def get_weather(city: str) -> str:
    """Legacy v1 weather tool — always fails after the Q2 drift."""
    raise RuntimeError("weather_v1 drift: 'city' arg rejected by upstream")


@tool
def fetch_forecast(location: str) -> str:
    """v2 weather tool — the documented successor."""
    return f"forecast for {location}: sunny, 27C"


TOOLS = {"get_weather": get_weather, "fetch_forecast": fetch_forecast}
INTENT = "weather_lookup"


# --- graph ----------------------------------------------------------------
class State(TypedDict):
    query: str
    result: str
    rulebook_path: str


def dispatch(state: State, config: RunnableConfig) -> State:
    """Every call consults the rulebook FIRST. If a documented substitution
    is already promoted, we route straight to the successor — the failing
    v1 tool is never invoked. No self-heal telemetry needed."""
    rb = Rulebook(path=Path(state["rulebook_path"]))
    r = heal.route(
        TOOLS, "get_weather", {"city": state["query"]},
        rulebook=rb, intent=INTENT, config=config,
    )
    return {**state, "result": str(r)}


def build_app():
    b = StateGraph(State)
    b.add_node("dispatch", dispatch)
    b.set_entry_point("dispatch")
    b.add_edge("dispatch", END)
    return b.compile()


# --- run ------------------------------------------------------------------
def main() -> None:
    here = Path(__file__).parent
    docs_dir = here / "sample_docs"
    store_path = here / "experience_phase4.db"
    rulebook_path = here / "rulebook_phase4.json"
    for p in (store_path, rulebook_path):
        if p.exists():
            p.unlink()

    # -- 4.1 — ingest the docs ------------------------------------------
    print(f"=== Ingesting {docs_dir} ===")
    seed = from_docs(docs_dir)
    print(f"  extracted {len(seed.assertions)} assertions")
    print(f"  breakdown: {json.dumps(seed.stats())}")

    print("\n=== A few sample assertions ===")
    for a in seed.assertions[:8]:
        print(f"  [{a.predicate}] {a.subject} -> {a.object}  ({a.source_uri})")

    print("\n=== `find('weather')` — semantic lookup over the seed ===")
    for a in seed.find("weather", k=4):
        print(f"  [{a.predicate}] {a.subject} -> {a.object}")

    # -- 4.2 — mine the docs -------------------------------------------
    print("\n=== DocsMiner proposals (from docs, zero episodes) ===")
    docs_miner = DocsMiner(seed=seed, default_intent=INTENT)
    proposals = docs_miner.mine([])
    for p in proposals:
        print(f"  [{p.kind}] {p.subject}  conf={p.confidence:.2f}")
        print(f"     evidence     : {p.evidence}")
        print(f"     change       : {json.dumps(p.change, default=str)}")

    # -- 4.3 — promote the weather migration ---------------------------
    rulebook = Rulebook(path=rulebook_path)
    weather_props = [p for p in proposals
                     if p.change.get("replace_skill") == "get_weather"
                     and p.change.get("with_skill") == "fetch_forecast"]
    if not weather_props:
        print("\n!! no weather migration proposal — extractor bug, aborting")
        return
    rule = rulebook.promote(weather_props[0], approved_by="ram@graxella")
    print(f"\n=== Promoted {weather_props[0].id} -> {rule.id} ===")
    print(f"  recipe: {json.dumps(rule.recipe, default=str)}")

    # -- 4.4 — first-ever traffic uses the promoted rule ---------------
    print("\n=== First-ever traffic — rulebook bypass active from turn 1 ===")
    store = SqliteExperienceStore(store_path)
    app = build_app()
    for i, q in enumerate(["Bengaluru", "Chennai", "Mumbai"], 1):
        cb = GraxellaCallback(store=store, session_id=f"p4-{i}",
                              default_intent=INTENT)
        out = app.invoke(
            {"query": q, "result": "", "rulebook_path": str(rulebook_path)},
            config={"callbacks": [cb], "metadata": {"intent": INTENT}},
        )
        print(f"  run {i}: {q!r:12} -> {out['result'][:60]}")

    print("\n=== Episodes (expect fetch_forecast=ok only — v1 never invoked) ===")
    for e in store.all():
        tcs = " | ".join(f"{tc.tool_id}={'ok' if tc.ok else 'ERR'}"
                         for tc in e.tool_calls) or "(no tools)"
        print(f"  {e.id[:14]}  session={e.session_id:8} tools=[{tcs}]")

    store.close()
    print("\ndone. Docs -> proposal -> promotion -> runtime bypass, from "
          "turn 1, no lived failures needed.")


if __name__ == "__main__":
    main()
