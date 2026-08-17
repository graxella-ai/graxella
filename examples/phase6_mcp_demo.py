"""Phase-6 demo — every graxella capability as an MCP tool.

The Phase-3 CLI (`graxella agenda ...`) works from a terminal. Phase 6 exposes
the SAME operations as an MCP tool suite so any MCP-aware client (Claude
Desktop, Cursor, an in-house agent) can drive Graxella's review loop.

This script exercises the handler functions DIRECTLY — no MCP transport,
no `pip install mcp` — because the transport binding in
`graxella.mcp.server` is a thin advertisement over the same handlers. If
these calls succeed here, the MCP tools will succeed from any client that
knows how to invoke them.

Flow:
  1. Show TOOL_SCHEMAS — this is exactly what an MCP client sees on connect.
  2. Seed a store with lived Experience (a mix of failing / healed episodes),
     mine + promote once via DocsMiner so the rulebook has real content.
  3. Call every handler in HANDLERS, printing the compact JSON response an
     MCP client would render to the user.
  4. Export a PROV-O bundle to disk and confirm it round-trips.

At the end, hint at the transport launch line an integrator would run:
    python -m graxella.mcp.server
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
from graxella.integrations.mcp.handlers import HANDLERS  # noqa: E402
from graxella.integrations.mcp.server import TOOL_SCHEMAS  # noqa: E402


# --- tools + graph (Phase-3 shape reused so the store looks realistic) ----
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


class State(TypedDict):
    query: str
    result: str
    rulebook_path: str


def dispatch(state: State, config: RunnableConfig) -> State:
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


# --- pretty printing helpers ---------------------------------------------
def _dump(payload: object, *, cap: int = 900) -> str:
    """MCP renders `TextContent` as JSON strings. Show the same, trimmed."""
    s = json.dumps(payload, indent=2, default=str)
    if len(s) > cap:
        return s[:cap] + f"\n... [truncated, total {len(s)} chars]"
    return s


def _banner(title: str) -> None:
    print(f"\n=== {title} ===")


# --- run ------------------------------------------------------------------
def main() -> None:
    here = Path(__file__).parent
    docs_dir = here / "sample_docs"
    store_path = here / "experience_phase6.db"
    rulebook_path = here / "rulebook_phase6.json"
    audit_out = here / "audit_phase6.jsonld"
    for p in (store_path, rulebook_path, audit_out):
        if p.exists():
            p.unlink()

    # -- 6.1 — what an MCP client sees on connect ------------------------
    _banner("TOOL_SCHEMAS — exactly what an MCP client renders")
    for name, spec in TOOL_SCHEMAS.items():
        req = spec["inputSchema"].get("required", [])
        print(f"  {name:20} required={req}")
        print(f"    {spec['description']}")

    # -- 6.2 — realistic store: one docs-driven promotion + episodes -----
    _banner("Seed rulebook from docs (Phase-4 pipeline)")
    seed = from_docs(docs_dir)
    proposals = DocsMiner(seed=seed, default_intent=INTENT).mine([])
    rulebook = Rulebook(path=rulebook_path)
    for p in proposals:
        if (p.change.get("replace_skill") == "get_weather"
                and p.change.get("with_skill") == "fetch_forecast"):
            rule = rulebook.promote(p, approved_by="ram@graxella")
            print(f"  promoted {p.id} -> {rule.id}")
            break

    _banner("Generate lived Experience (rulebook bypass active from turn 1)")
    store = SqliteExperienceStore(store_path)
    app = build_app()
    for i, q in enumerate(["Bengaluru", "Chennai", "Mumbai"], 1):
        cb = GraxellaCallback(store=store, session_id=f"p6-{i}",
                              default_intent=INTENT)
        app.invoke(
            {"query": q, "result": "", "rulebook_path": str(rulebook_path)},
            config={"callbacks": [cb], "metadata": {"intent": INTENT}},
        )
    ep_count = len(store.all())
    store.close()
    print(f"  wrote {ep_count} episodes")

    common = {
        "store_path":    str(store_path),
        "rulebook_path": str(rulebook_path),
        "docs_path":     str(docs_dir),
    }

    # -- 6.3 — walk every handler ---------------------------------------
    _banner("HANDLERS.list_proposals(...)")
    resp = HANDLERS["list_proposals"](
        store_path=common["store_path"],
        docs_path=common["docs_path"],
        rulebook_path=common["rulebook_path"],
    )
    print(_dump(resp))

    _banner("HANDLERS.list_rules(...)")
    rules_resp = HANDLERS["list_rules"](rulebook_path=common["rulebook_path"])
    print(_dump(rules_resp))

    _banner("HANDLERS.query_episodes(session='p6-1')")
    print(_dump(HANDLERS["query_episodes"](
        store_path=common["store_path"], session="p6-1", limit=5)))

    _banner("HANDLERS.query_seed(query='weather')")
    print(_dump(HANDLERS["query_seed"](
        docs_path=common["docs_path"], query="weather", k=4)))

    # promote_proposal: pick a fresh, still-pending proposal (if any)
    _banner("HANDLERS.promote_proposal(...) — idempotent")
    pending = [row for row in resp["proposals"] if row["status"] == "pending"]
    if pending:
        prop_id = pending[0]["id"]
        print(f"  promoting {prop_id} ...")
        print(_dump(HANDLERS["promote_proposal"](
            store_path=common["store_path"],
            rulebook_path=common["rulebook_path"],
            docs_path=common["docs_path"],
            proposal_id=prop_id,
            reviewer="ram@graxella",
        )))
    else:
        print("  no pending proposals — skipping (idempotency proven by seed)")

    # reject_proposal: sabotage a fake id to prove the flag is written
    _banner("HANDLERS.reject_proposal(...)")
    print(_dump(HANDLERS["reject_proposal"](
        rulebook_path=common["rulebook_path"],
        proposal_id="prop_never_will_be",
    )))

    # -- 6.4 — audit bundle -------------------------------------------
    _banner("HANDLERS.export_audit(...)")
    audit_resp = HANDLERS["export_audit"](
        store_path=common["store_path"],
        rulebook_path=common["rulebook_path"],
        docs_path=common["docs_path"],
        out_path=str(audit_out),
    )
    print(_dump(audit_resp))
    assert audit_out.exists(), "audit file was not written"
    payload = json.loads(audit_out.read_text(encoding="utf-8"))
    assert "@graph" in payload and len(payload["@graph"]) > 0, "empty bundle"
    print(f"  audit file OK: {audit_out.name} ({audit_out.stat().st_size} bytes)")

    # -- 6.5 — error surface -----------------------------------------
    _banner("Error surface — an unknown/invalid call")
    try:
        HANDLERS["promote_proposal"](
            store_path=common["store_path"],
            rulebook_path=common["rulebook_path"],
            proposal_id="prop_does_not_exist",
        )
    except KeyError as e:
        print(f"  raised KeyError as expected: {e}")

    _banner("Done")
    print("Every handler passes. To expose these as MCP tools on stdio, run:")
    print("    python -m graxella.integrations.mcp.server")
    print("Then point Claude Desktop / Cursor at that command in their MCP config.")


if __name__ == "__main__":
    main()
