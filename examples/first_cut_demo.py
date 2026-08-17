"""First-cut demo — hidden agenda end-to-end on a real AXON runtime.

Wires:
  1. A default_local_runtime() from axon_runtime.
  2. Two toy agents: `weather_v1` (broken) and `weather_v2` (works).
  3. graxella.attach(runtime, store) — instrumentation.
  4. A handful of dispatches that reproduce the "v1 fails, v2 succeeds"
     pattern the RuleDistiller is designed to catch.
  5. The offline HiddenAgendaRunner. Proposals printed with their evidence
     chain back to the exact episode ids.

Success criteria: this file, run as-is, prints the Experience rows and at
least one Proposal with derived_from populated.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

# Path shim so the demo runs before graxella / axon_runtime are pip-installed.
_REPO = Path(__file__).resolve().parents[2]
for _sub in ("graxella", "axon_runtime"):
    p = _REPO / _sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from axon_runtime.contrib.callable_agent import CallableAgent  # noqa: E402
from axon_runtime.contrib.base import AgentContext  # noqa: E402
from axon_runtime.core.side_effect import SideEffect  # noqa: E402
from axon_runtime.planes.data import SkillDecl  # noqa: E402
from axon_runtime.runtime import default_local_runtime  # noqa: E402

from graxella import InMemoryExperienceStore  # noqa: E402
from graxella.agenda import HiddenAgendaRunner  # noqa: E402
from graxella.integrations.axon import attach  # noqa: E402


# --- toy agents -------------------------------------------------------------
def _v1_handler(ctx: AgentContext) -> dict[str, Any]:
    raise RuntimeError("weather_v1: schema drift — 'city' arg no longer accepted")


def _v2_handler(ctx: AgentContext) -> dict[str, Any]:
    return {"forecast": "sunny", "source": "weather_v2"}


V1_SKILL = SkillDecl(
    name="get_weather",
    description="Fetch a weather forecast (v1 schema).",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
    output_schema={"type": "object"},
    side_effect_class="read_only",
)
V2_SKILL = SkillDecl(
    name="fetch_forecast",
    description="Fetch a weather forecast (v2 schema).",
    input_schema={"type": "object", "properties": {"location": {"type": "string"}}},
    output_schema={"type": "object"},
    side_effect_class="read_only",
)


def build_agents() -> tuple[CallableAgent, CallableAgent]:
    v1 = CallableAgent(
        name="weather_v1",
        description="Legacy weather agent; v1 schema.",
        handler=_v1_handler,
        skills=[V1_SKILL],
    )
    v2 = CallableAgent(
        name="weather_v2",
        description="Current weather agent; v2 schema.",
        handler=_v2_handler,
        skills=[V2_SKILL],
    )
    return v1, v2


def run_pair(runtime: Any, task: str, session_id: str) -> None:
    """Reproduce the (fail v1 → succeed v2) sequence RuleDistiller looks for."""
    runtime.dispatch(
        task=task, session_id=session_id,
        intent="weather_lookup", skill="get_weather",
        side_effect=SideEffect.READ_ONLY,
        payload={"city": "Bengaluru"},
    )
    runtime.dispatch(
        task=task, session_id=session_id,
        intent="weather_lookup", skill="fetch_forecast",
        side_effect=SideEffect.READ_ONLY,
        payload={"location": "Bengaluru"},
    )


def main() -> None:
    rt = default_local_runtime()
    v1, v2 = build_agents()
    rt.register_agent(v1)
    rt.register_agent(v2)

    store = InMemoryExperienceStore()
    attach(rt, store)

    # Two independent sessions on the same intent — enough for RuleDistiller
    # (min_support=2) to promote the pattern from noise to a rule candidate.
    run_pair(rt, "what's the weather today?", f"sess-a-{int(time.time())}")
    run_pair(rt, "weather please", f"sess-b-{int(time.time())}")

    # A pure-v2 call so CapabilityReweigher / TrustPromoter also have signal.
    rt.dispatch(
        task="weather please", session_id=f"sess-c-{int(time.time())}",
        intent="weather_lookup", skill="fetch_forecast",
        side_effect=SideEffect.READ_ONLY,
        payload={"location": "Chennai"},
    )

    # --- print Experience rows ---------------------------------------------
    print("\n=== Experience rows ===")
    for e in store.all():
        agent = e.agent_uri.split("/")[-1] if e.agent_uri else "-"
        print(f"  {e.id[:14]}  intent={e.intent:16}  skill={str(e.skill):18}"
              f"  agent={agent:12}  ok={e.ok}")

    # --- run the hidden agenda ---------------------------------------------
    runner = HiddenAgendaRunner(store=store)
    proposals = runner.run()

    print(f"\n=== Hidden Agenda proposals ({len(proposals)}) ===")
    for p in proposals:
        print(f"\n  [{p.kind}] {p.subject}   (conf={p.confidence:.2f})")
        print(f"     evidence     : {p.evidence}")
        print(f"     derived_from : {p.derived_from}")
        print(f"     change       : {p.change}")

    out = Path(__file__).parent / "proposals.json"
    runner.dump(out)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
