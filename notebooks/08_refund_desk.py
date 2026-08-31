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

The whole substrate rides on ONE object: ``graxella.Session`` owns the
governance tuple (memory, rulebook, gate, domain, model_id); the
``@grx.tool`` decorator returns real LangChain tools, so agents are
built with plain ``create_agent`` — graxella is invisible at the
framework boundary.

Run from the repo root (Ollama must be serving qwen2.5:3b):
  uv run python notebooks/08_refund_desk.py
"""
from __future__ import annotations

import tempfile
import time

from langchain.agents import create_agent
from langchain_ollama import ChatOllama

import graxella

MODEL = "qwen2.5:3b"
grx = graxella.Session("refund-desk", domain="refunds", model_id=MODEL,
                       workdir=tempfile.mkdtemp(prefix="refund-desk-"))

# ---------------------------------------------------------------- fake ERP
ORDERS = {
    "1042": {"item": "espresso machine", "delivered": True, "days_ago": 6},
    "2077": {"item": "desk lamp", "delivered": True, "days_ago": 40},
}


@grx.tool
def check_order(order_id: str) -> str:
    """look up an order: item, delivery status and age in days"""
    o = ORDERS.get(order_id.strip())
    return (f"order {order_id}: {o['item']}, delivered={o['delivered']}, "
            f"{o['days_ago']} days ago") if o else f"order {order_id}: not found"


@grx.tool
def lookup_policy(topic: str) -> str:
    """look up the refund policy for damaged or late items"""
    return ("policy: damaged items refundable within 30 days of delivery; "
            "after 30 days offer store credit only")


# The drifted internal API, wrapped in the heal ladder ---------------------
def shipping_v2(args: dict) -> str:
    return f"shipment for order {args['order_ref']}: delivered, signed"


@grx.healer
def llm_healer(tool_name: str, args: dict, error: str) -> graxella.TransformRecipe:
    """The ONE place an LLM appears in healing — and only once, ever."""
    print(f"    [healer: asking {MODEL} to map args for {tool_name} ...]")
    llm = ChatOllama(model=MODEL, temperature=0)
    reply = llm.invoke(
        f"An API call failed: {error}. Old args: {sorted(args)}. The new "
        f"schema expects field 'order_ref'. Answer with ONLY the old field "
        f"name that should be renamed to order_ref.").content.strip()
    old_field = reply.split()[0].strip("'\"`.,") if reply else "order_id"
    print(f"    [healer proposed: {old_field} -> order_ref]")
    return graxella.TransformRecipe(field_map={old_field: "order_ref"})


@grx.tool(fallback=shipping_v2)
def get_shipping_status(order_id: str) -> str:
    """get the live shipping status for an order"""
    # the legacy v1 endpoint, silently retired last sprint:
    raise RuntimeError("HTTP_410_GONE: schema deprecated, use shipping.v2")


# ---------------------------------------------------------------- the mesh
@grx.tool
def send_email(body: str) -> str:
    """send a friendly apology email response to the customer about their refund"""
    return f"email queued ({len(body)} chars)"


llm = ChatOllama(model=MODEL, temperature=0)
triage = create_agent(
    llm, [check_order, lookup_policy, get_shipping_status], name="triage")
responder = create_agent(llm, [send_email], name="responder")

app = grx.mesh([triage, responder])

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
    t = app.run_trajectory(req, max_hops=3)
    took = time.perf_counter() - t0
    print(f"\n[{i}] {req[:64]}...")
    print(f"    hops={[h.agent for h in t.hops]}  status={t.status}  "
          f"({took:.0f}s)")
    print(f"    reply: {t.final_response[:150].strip()!r}")

print("\n" + "=" * 66)
print("THE DRIFTED SHIPPING API — fail once, learn forever (real LLM heal)")
print("=" * 66)
print("call 1:", get_shipping_status.invoke({"order_id": "1042"}))
pend = grx.pending()
if pend:
    print(f"proposal queued for review: {pend[0]['proposal_id']}")
    grx.approve(pend[0], by="operator:sridhar", note="rename verified in demo")
    print("approved + promoted to the rulebook")
print("call 2:", get_shipping_status.invoke({"order_id": "2077"}))
print(f"LLM healer invocations, total: {grx.healer_calls}")

print("\n" + "=" * 66)
print("THE LEDGER ANSWERS")
print("=" * 66)
stats = grx.stats()["total"]
print(f"outcomes={stats['count']}  ok_rate={stats['ok_rate']}  "
      f"tokens={stats['tokens_in']}+{stats['tokens_out']}  "
      f"avg_latency_ms={stats['avg_latency_ms']}")
recall_events = app.tracer.events(event_type="recall.injected")
print(f"case-recall injections: {len(recall_events)} "
      f"(request 3 saw requests 1-2 as verified experience)")
if pend:
    print("\ngate.why(shipping heal):")
    print(grx.why(pend[0]))

print(f"\ntopology map written: {grx.save_topology()}")
print(f"workdir (ledger, rulebook, WAL): {grx.workdir}")

# Live operator view (blocking) — Swagger UI at /docs, nodes-and-edges
# agent map at /topology, metrics at /stats and /trust:
#   grx.serve(port=8077)
