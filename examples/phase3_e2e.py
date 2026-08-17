"""Phase-3 demo — close the intelligence loop end-to-end.

The loop:

     [Episodes captured]  -->  hidden agenda  -->  proposals
                                                       |
                                                    reviewer promotes
                                                       |
                                                       v
     [next run]  -->  heal.route(rulebook) bypass  -->  approved path

What this script shows in order:

  1. Run the LangGraph app with GraxellaCallback.
     Weather queries route to a HealedTool that self-heals get_weather -> fetch_forecast.
     Every heal is recorded as (err ToolCall, ok ToolCall) on the same Episode.

  2. Mine the store. The RuleDistiller emits a
        get_weather -> fetch_forecast   substitution proposal.

  3. Promote it into the rulebook (simulating a human reviewer clicking approve).

  4. Re-run the SAME queries — but this time via heal.route(rulebook=..., intent=...).
     The dispatcher consults the rulebook FIRST, so it never invokes get_weather.
     The Experience row for the second batch has ONE ok ToolCall on fetch_forecast,
     no errors. That's the visible proof the loop closed.

  5. Export a PROV-O JSON-LD audit bundle — the file an auditor reads.

Zero LLMs. Zero retries in the post-promotion path. Compile-time intelligence,
runtime is dumb.
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

from graxella import Rulebook, SqliteExperienceStore  # noqa: E402
from graxella.agenda import HiddenAgendaRunner  # noqa: E402
from graxella.audit import export as audit_export  # noqa: E402
from graxella.healing import recipes as heal  # noqa: E402
from graxella.integrations.langgraph import GraxellaCallback  # noqa: E402


# --- tools ----------------------------------------------------------------
@tool
def get_weather(city: str) -> str:
    """Fetch a current weather report for the given city name."""
    raise RuntimeError("weather_v1 drift: 'city' arg rejected by upstream")


@tool
def fetch_forecast(location: str) -> str:
    """Fetch current weather forecast (temperature, sky) for a location."""
    return f"forecast for {location}: sunny, 27C"


TOOLS = {"get_weather": get_weather, "fetch_forecast": fetch_forecast}
INTENT = "weather_lookup"

# The pre-promotion behaviour: wrap get_weather so failures fall back to
# fetch_forecast via a hand-written recipe. This is what generates the
# (err, ok) telemetry the miner uses.
HEALED_WEATHER = heal.wrap(
    get_weather, fallback=fetch_forecast,
    recipe=heal.TransformRecipe(field_map={"city": "location"}),
)


# --- graph ----------------------------------------------------------------
class State(TypedDict):
    query: str
    result: str
    rulebook_path: str


def dispatch_pre(state: State, config: RunnableConfig) -> State:
    """Pre-promotion dispatcher — legacy tool + heal.wrap."""
    r = HEALED_WEATHER({"city": state["query"]}, config=config)
    return {**state, "result": str(r)}


def dispatch_post(state: State, config: RunnableConfig) -> State:
    """Post-promotion dispatcher — consult the rulebook, route directly."""
    rb = Rulebook(path=Path(state["rulebook_path"]))
    r = heal.route(
        TOOLS, "get_weather", {"city": state["query"]},
        rulebook=rb, intent=INTENT, config=config,
    )
    return {**state, "result": str(r)}


def build_app(dispatcher):
    b = StateGraph(State)
    b.add_node("dispatch", dispatcher)
    b.set_entry_point("dispatch")
    b.add_edge("dispatch", END)
    return b.compile()


# --- run ------------------------------------------------------------------
QUERIES = ["Bengaluru", "Chennai", "Mumbai"]


def _run_batch(app, store: SqliteExperienceStore, *,
               tag: str, rulebook_path: str) -> None:
    for i, q in enumerate(QUERIES, 1):
        cb = GraxellaCallback(
            store=store, session_id=f"{tag}-{i}",
            default_intent=INTENT,
        )
        out = app.invoke(
            {"query": q, "result": "", "rulebook_path": rulebook_path},
            config={"callbacks": [cb], "metadata": {"intent": INTENT}},
        )
        print(f"  {tag} run {i}: {q!r:12} -> {out['result'][:60]}")


def _print_episodes(store: SqliteExperienceStore, *, header: str,
                    session_prefix: str) -> None:
    print(f"\n=== {header} ===")
    matching = [e for e in store.all() if e.session_id.startswith(session_prefix)]
    for e in sorted(matching, key=lambda x: x.started_at):
        tcs = " | ".join(f"{tc.tool_id}={'ok' if tc.ok else 'ERR'}"
                         for tc in e.tool_calls) or "(no tools)"
        print(f"  {e.id[:14]}  session={e.session_id:10} tools=[{tcs}]")


def main() -> None:
    here = Path(__file__).parent
    store_path = here / "experience_phase3.db"
    rulebook_path = here / "rulebook_phase3.json"
    audit_path = here / "audit_phase3.jsonld"
    for p in (store_path, rulebook_path, audit_path):
        if p.exists():
            p.unlink()

    store = SqliteExperienceStore(store_path)
    rulebook = Rulebook(path=rulebook_path)

    # -- Phase 3.1 — capture pre-promotion evidence ---------------------
    print("=== Batch 1 (pre-promotion): heal.wrap self-heals every call ===")
    app_pre = build_app(dispatch_pre)
    _run_batch(app_pre, store, tag="pre", rulebook_path=str(rulebook_path))
    _print_episodes(store, header="Pre-promotion Episodes (expect err+ok pair)",
                    session_prefix="pre-")

    # -- Phase 3.2 — mine the store ------------------------------------
    print("\n=== Mining hidden agenda ===")
    proposals = HiddenAgendaRunner(store=store).run()
    for p in proposals:
        print(f"  [{p.kind}] {p.subject}  conf={p.confidence:.2f}")
        print(f"     evidence     : {p.evidence}")
        print(f"     derived_from : {p.derived_from}")

    # -- Phase 3.3 — promote the substitution rule ----------------------
    sub_proposals = [p for p in proposals
                     if p.kind == "rule"
                     and p.change.get("replace_skill") == "get_weather"
                     and p.change.get("with_skill") == "fetch_forecast"]
    if not sub_proposals:
        print("\n!! no substitution proposal to promote — aborting demo")
        store.close()
        return

    target = sub_proposals[0]
    # Attach a recipe manually — Phase 4 will have B10's TransformCompiler
    # emit this automatically from the (err_call, ok_call) evidence pair.
    target.change["recipe"] = {"field_map": {"city": "location"}}
    rule = rulebook.promote(target, approved_by="ram@graxella")
    print(f"\n=== Promoted proposal {target.id} -> rule {rule.id} ===")
    print(f"  rulebook file: {rulebook_path}")

    # -- Phase 3.4 — re-run with rulebook-aware dispatcher --------------
    print("\n=== Batch 2 (post-promotion): heal.route bypasses get_weather ===")
    app_post = build_app(dispatch_post)
    _run_batch(app_post, store, tag="post", rulebook_path=str(rulebook_path))
    _print_episodes(store,
                    header="Post-promotion Episodes (expect fetch_forecast=ok only)",
                    session_prefix="post-")

    # -- Phase 3.5 — export PROV-O audit --------------------------------
    print("\n=== Exporting PROV-O JSON-LD audit ===")
    proposals_now = HiddenAgendaRunner(store=store).run()
    payload = audit_export(store, rulebook, proposals_now, path=audit_path)
    n_nodes = len(payload["@graph"])
    print(f"  wrote {audit_path}")
    print(f"  {n_nodes} PROV nodes (episodes + miner + proposals + promotions + rules)")

    # Quick sanity peek at what the auditor sees
    kinds: dict[str, int] = {}
    for node in payload["@graph"]:
        k = node.get("grx:kind", "?")
        kinds[k] = kinds.get(k, 0) + 1
    print(f"  node breakdown: {json.dumps(kinds)}")

    store.close()
    print("\ndone. The loop is closed: evidence -> proposal -> promotion -> "
          "runtime bypass -> audit trail.")


if __name__ == "__main__":
    main()
