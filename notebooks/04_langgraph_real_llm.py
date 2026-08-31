"""Showcase 04 -- mixed agent shapes wrapped by graxella.mesh().

PROVES the six things graxella solves ON A REAL WORKLOAD -- with the
developer's choice of agent syntax. Every shape lands in the same
mesh with the same governance underneath.

  1. MIXED agents  -- native ``create_react_agent(...)`` +
                      ``graxella.Agent(role=..., tools=...)`` +
                      plain callable, ALL in one mesh([...]).
  2. wrap          -- ``graxella.mesh([...])`` returns a LangGraph-shape
                      Runnable with .memory / .society / .tracer / .gate.
  3. peer-aware    -- each agent's system prompt is auto-augmented with
                      a directory of peers so it can suggest handoffs.
  4. route         -- deterministic TF-IDF picks the right agent
                      (or swap to a small transformer with
                      ``router="transformer"``).
  5. dispatch      -- the real LLM runs and actually invokes its tools.
  6. constitution + gate + why() -- all fire against the real workload.

Prereqs: Ollama daemon running with ``qwen2.5:3b`` pulled.
Run:
    .venv/Scripts/python showcase/04_langgraph_real_llm.py   (Windows)
    .venv/bin/python     showcase/04_langgraph_real_llm.py   (macOS/Linux)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "graxella"))

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

import graxella
from graxella import (Agent, Constitution, GatePolicy, Memory, ObjectiveScores,
                      PromotionGate, UnifiedTracer)


# ---------------- tools bound to the native LangGraph agents ------------

@tool
def check_order(order_id: str) -> dict:
    """Look up an order's status and total by its id."""
    fake = {
        "1234": {"status": "delivered_damaged", "total_usd": 47.50},
        "9999": {"status": "in_transit",        "total_usd": 210.00},
    }
    return {"order_id": order_id, **fake.get(order_id, {"status": "unknown"})}


@tool
def lookup_policy(topic: str) -> str:
    """Look up the company policy on a topic (e.g. damaged, refund, shipping)."""
    return {
        "damaged":  "Refunds under $50 for damaged goods are auto-approved.",
        "refund":   "Standard refund window is 30 days from delivery.",
        "shipping": "Expedited shipping is complimentary for orders over $100.",
    }.get(topic.lower(), "No specific policy on that topic; escalate to a manager.")


@tool
def draft_email(to: str, subject: str, body: str) -> dict:
    """Draft (but do not send) a customer email."""
    return {"to": to, "subject": subject, "body": body,
            "sent": False, "requires_review": True}


@tool
def compute_refund_rate(_input: str = "") -> str:
    """Compute the refund rate for the last 30 days."""
    return "refund_rate_last_30d = 3.7% (stub)"


def section(t: str) -> None:
    print(f"\n{'-' * 76}\n{t}\n{'-' * 76}")


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="graxella-showcase-04-"))
    print(f"[setup] workdir: {workdir}")
    print(f"[setup] LLM:     Ollama qwen2.5:3b (must be listening on localhost:11434)")

    llm = ChatOllama(model="qwen2.5:3b", temperature=0)

    # (1) --- MIXED agent shapes -- developer's choice, same mesh -------
    section("(1) AGENTS -- native LangGraph + graxella.Agent + plain callable")

    # Path A: pure LangGraph.
    triage = create_react_agent(
        llm,
        tools=[check_order, lookup_policy],
        name="triage",
    )
    # Path B: graxella.Agent (CrewAI-shape). Same wire underneath -- graxella
    # compiles this to a create_react_agent loop when llm+tools are set.
    responder = Agent(
        role="responder",
        goal="draft empathetic email replies to customers",
        backstory="You draft short, warm emails. Always call draft_email once.",
        tools=[draft_email],
        llm=llm,
    )
    # Path C: plain callable -- no LLM, deterministic stub. Also welcome.
    analyst = create_react_agent(
        llm,
        tools=[compute_refund_rate],
        name="analyst",
    )

    print(f"  triage     = create_react_agent(llm, tools=[...], name='triage')   # native LangGraph")
    print(f"  responder  = graxella.Agent(role='responder', tools=[...], llm=llm)  # CrewAI-shape")
    print(f"  analyst    = create_react_agent(llm, tools=[...], name='analyst')  # native LangGraph")

    # (2) --- one call wraps them all -----------------------------------
    section("(2) WRAP -- graxella.mesh([triage, responder, analyst], router='tfidf')")

    memory = Memory.sqlite(db_path=str(workdir / "mnema.db"),
                           agent_id="support-runtime")
    tracer = UnifiedTracer.default()
    gate = PromotionGate(
        threshold=0.85, require_human=True,
        policy=GatePolicy(
            weights={"quality": 0.4, "compliance": 0.3, "cost": 0.2, "latency": 0.1},
            cost_reference=0.10, latency_reference=500.0,
            compliance_floor=0.9, auto_approve=0.85, needs_human_min=0.5,
        ),
    )
    constitution = Constitution.from_dict({
        "version": "1.0",
        "invariants": [{
            "name": "outbound_email.requires_review",
            "applies_to": "delegate", "severity": "warning",
            "predicate": {
                "type": "object",
                "properties": {
                    "chosen_agent": {"not": {"const": "responder"}},
                },
            },
        }],
    })

    app = graxella.mesh(
        [triage, responder, analyst],
        memory=memory,
        tracer=tracer,
        gate=gate,
        constitution=constitution,
        router="tfidf",            # or "transformer" -- opt in when you install sentence-transformers
        store_path=str(workdir / "routes.jsonl"),
    )
    print(f"  agents registered: {app.society.agents()}")
    print("  (each agent's system prompt now includes a peer-directory of the others)")
    print("  (router='tfidf' -- zero-LLM routing decisions; swap to 'transformer' for MiniLM)")

    # (3) --- LangGraph-shape .invoke drives real LLM tool calls --------
    section("(3) INVOKE + DISPATCH -- native runnable, real LLM tool-calls")
    queries = [
        "Customer wants refund on order 1234, arrived damaged.",
        "Draft a reply to alice@example.com confirming her refund.",
        "What was our refund rate last month?",
    ]
    decision_ids = []
    for q in queries:
        print(f"\n  > {q}")
        # Same shape as a raw LangGraph invoke -- messages in, messages out.
        out = app.invoke({"messages": [("user", q)]})
        route = out["route"]
        print(f"    routed -> {route['agent']}::{route['skill']} "
              f"score={route['score']:.3f}  strategy={route['strategy']}")
        content = out["messages"][-1]["content"]
        snip = content if len(content) < 180 else content[:180] + "..."
        print(f"    output: {snip}")
        if route.get("decision_id"):
            decision_ids.append(route["decision_id"])

    # (4) --- constitution ---------------------------------------------
    section("(4) CONSTITUTION -- 'responder' pick trips the invariant")
    viols = tracer.events(event_type="governance.constitution_violation")
    print(f"  {len(viols)} violation(s) recorded (detection-only, never blocks)")
    for v in viols:
        p = v.payload
        print(f"    [{p['severity']}] {p['name']}: {p['detail']}")

    # (5) --- gate scoring ---------------------------------------------
    section("(5) GATE -- three learning proposals")
    proposals = [
        ("route.tag_add",
         {"agent": "triage", "add_terms": ["damaged", "return"]},
         "narrow",
         ObjectiveScores(cost_usd=0.02, latency_ms=120, quality=0.95, compliance=1.0)),
        ("rule.new",
         {"pattern": "auto-approve refunds under $50 for damaged goods"},
         "wide",
         ObjectiveScores(cost_usd=0.02, latency_ms=120, quality=0.95, compliance=1.0)),
        ("skill.new",
         {"agent": "responder", "skill": "send_unreviewed_email"},
         "narrow",
         ObjectiveScores(cost_usd=0.02, latency_ms=120, quality=0.95, compliance=0.4)),
    ]
    for kind, payload, blast, obj in proposals:
        p = gate.propose(kind, payload, blast_radius=blast, objectives=obj)
        decision, after = gate.auto_evaluate(p.id)
        print(f"  #{after.id} {after.kind:<14} blast={after.blast_radius:<7} "
              f"scalar={after.score:.3f} compliance={obj.compliance:.2f} "
              f"-> {decision.value.upper():<13} (status={after.status.value})")

    # (6) --- why ------------------------------------------------------
    section("(6) WHY -- cross-source provenance for the first decision")
    if decision_ids and decision_ids[0]:
        joint = app.why(decision_ids[0])
        print(f"  tracer chain events: {len(joint.get('tracer_chain', []))}")
        mnema = joint.get("mnema") or {}
        if isinstance(mnema, dict) and "error" not in mnema:
            print(f"  mnema keys:          {list(mnema.keys())}")

    print("\n" + "=" * 76)
    print(" SHOWCASE COMPLETE.  Native LangGraph agents, graxella-governed.")
    print(" Boot 03_dashboard.py to see this runtime live.")
    print("=" * 76)


if __name__ == "__main__":
    main()
