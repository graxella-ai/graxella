"""A practical use case: a customer-support refund desk — REAL LLMs.

Everything below runs on qwen2.5:3b via Ollama. Graxella never sees the
model choice: swap the ChatOllama line for any LangChain chat model and
the substrate — routing, recall, trajectories, healing, gate, ledger —
behaves identically (model-agnostic by construction; evidence is scoped
per model_id).

The business scenario:
  * customers write in about damaged orders and refund status
  * TRIAGE (LLM + order/policy tools) decides eligibility, then hands
    off to RESPONDER via the typed HANDOFF marker
  * RESPONDER (LLM) writes the customer email
  * the internal shipping-status API silently v2-drifted last sprint —
    the heal ladder catches it: heal once, gate, promote, never again

Run from the repo root (Ollama must be serving qwen2.5:3b):
  uv run python showcase/08_refund_desk.py
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

import graxella
from axon_fabric.interceptor import ToolInterceptor
from graxella.api.topology import render_html, topology_data
from graxella.beliefs import Memory
from graxella.gate.evidence import EvidenceGate, pending_from_ledger
from graxella.healing import TransformRecipe
from graxella.rulebook import Rulebook
from graxella.trajectory import TrajectoryBudget

MODEL = "qwen2.5:3b"
work = Path(tempfile.mkdtemp(prefix="refund-desk-"))
memory = Memory.sqlite(str(work / "mnema.db"), agent_id="refund-desk",
                       namespace="refunds", buffered=True)
rulebook = Rulebook(path=work / "rulebook.json")
gate = EvidenceGate(memory)

# ---------------------------------------------------------------- fake ERP
ORDERS = {
    "1042": {"item": "espresso machine", "delivered": True, "days_ago": 6},
    "2077": {"item": "desk lamp", "delivered": True, "days_ago": 40},
}


@tool
def check_order(order_id: str) -> str:
    """look up an order: item, delivery status and age in days"""
    o = ORDERS.get(order_id.strip())
    return (f"order {order_id}: {o['item']}, delivered={o['delivered']}, "
            f"{o['days_ago']} days ago") if o else f"order {order_id}: not found"


@tool
def lookup_policy(topic: str) -> str:
    """look up the refund policy for damaged or late items"""
    return ("policy: damaged items refundable within 30 days of delivery; "
            "after 30 days offer store credit only")


# The drifted internal API, wrapped in the heal ladder ---------------------
def shipping_v1(args: dict) -> str:
    raise RuntimeError("HTTP_410_GONE: schema deprecated, use shipping.v2")


def shipping_v2(args: dict) -> str:
    return f"shipment for order {args['order_ref']}: delivered, signed"


def llm_healer(tool_name: str, args: dict, error: str) -> TransformRecipe:
    """The ONE place an LLM appears in healing — and only once, ever."""
    print(f"    [healer: asking {MODEL} to map args for {tool_name} ...]")
    llm = ChatOllama(model=MODEL, temperature=0)
    reply = llm.invoke(
        f"An API call failed: {error}. Old args: {sorted(args)}. The new "
        f"schema expects field 'order_ref'. Answer with ONLY the old field "
        f"name that should be renamed to order_ref.").content.strip()
    old_field = reply.split()[0].strip("'\"`.,") if reply else "order_id"
    print(f"    [healer proposed: {old_field} -> order_ref]")
    return TransformRecipe(field_map={old_field: "order_ref"})


shipping_status = ToolInterceptor(
    shipping_v1, tool_name="shipping_status", memory=memory,
    rulebook=rulebook, gate=gate, fallback=shipping_v2, healer=llm_healer,
    domain="refunds", model_id=MODEL)


@tool
def get_shipping_status(order_id: str) -> str:
    """get the live shipping status for an order"""
    return shipping_status({"order_id": order_id})


# ---------------------------------------------------------------- the mesh
@tool
def send_email(body: str) -> str:
    """send a friendly apology email response to the customer about their refund"""
    return f"email queued ({len(body)} chars)"


llm = ChatOllama(model=MODEL, temperature=0)
triage = create_react_agent(
    llm, [check_order, lookup_policy, get_shipping_status], name="triage")
responder = create_react_agent(llm, [send_email], name="responder")

app = graxella.mesh(
    [triage, responder],
    memory=memory, store_path=str(work / "routes.jsonl"),
    domain="refunds", model_id=MODEL)

print("=" * 66)
print(f"REFUND DESK — two {MODEL} agents on the graxella substrate")
print("=" * 66)

REQUESTS = [
    "customer says order 1042 espresso machine arrived damaged, wants refund",
    "customer asks about refund for damaged order 2077 desk lamp",
    "customer reports order 1042 damaged again in a second email, refund?",
]
for i, req in enumerate(REQUESTS, 1):
    t0 = time.perf_counter()
    t = app.run_trajectory(req, budget=TrajectoryBudget(max_hops=3))
    took = time.perf_counter() - t0
    print(f"\n[{i}] {req[:64]}...")
    print(f"    hops={[h.agent for h in t.hops]}  status={t.status}  "
          f"({took:.0f}s)")
    print(f"    reply: {t.final_response[:150].strip()!r}")

print("\n" + "=" * 66)
print("THE DRIFTED SHIPPING API — fail once, learn forever (real LLM heal)")
print("=" * 66)
print("call 1:", shipping_status({"order_id": "1042"}))
pend = pending_from_ledger(memory)
if pend:
    pid = pend[0]["proposal_id"]
    print(f"proposal queued for review: {pid}")
    gate.approve(pid, by="operator:sridhar", note="rename verified in demo")
    _, approved = gate.decide(
        TransformRecipe(field_map={"order_id": "order_ref"}).to_proposal(
            domain="refunds", tool="shipping_status",
            origin="healer:axon-fabric", model_id=MODEL))
    rulebook.promote(approved, domain="refunds")
    print("approved + promoted to the rulebook")
print("call 2:", shipping_status({"order_id": "2077"}))
print(f"LLM healer invocations, total: {shipping_status.healer_calls}")

print("\n" + "=" * 66)
print("THE LEDGER ANSWERS")
print("=" * 66)
stats = memory.outcome_stats()["total"]
print(f"outcomes={stats['count']}  ok_rate={stats['ok_rate']}  "
      f"tokens={stats['tokens_in']}+{stats['tokens_out']}  "
      f"avg_latency_ms={stats['avg_latency_ms']}")
recall_events = app.tracer.events(event_type="recall.injected")
print(f"case-recall injections: {len(recall_events)} "
      f"(request 3 saw requests 1-2 as verified experience)")
if pend:
    print("\ngate.why(shipping heal):")
    print(gate.why(pend[0]["proposal_id"]))

topo = work / "topology.html"
topo.write_text(render_html(topology_data(app)), encoding="utf-8")
print(f"\ntopology map written: {topo}")
print(f"workdir (ledger, rulebook, WAL): {work}")
