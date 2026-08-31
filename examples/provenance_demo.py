"""Forward provenance -- 'touched' edges over the ledger.

graxella already records BACKWARD provenance (what evidence led to a belief).
Touched edges add the FORWARD direction -- what a decision affected -- so
provenance is a graph you can walk both ways. Two audit questions that were
previously unanswerable become one call each:

  ACT 1  "Show me everything that touched order:1234"  (across every agent)
  ACT 2  "This proposal is about to govern production -- which incident
          spawned it?"  (a live artifact traced back to its cause)

Fully deterministic -- no model required (a plain healer stands in for the
hidden one), so it runs anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "packages" / "graxella"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

import graxella  # noqa: E402


def main() -> None:
    grx = graxella.Session("ops-desk", domain="support", model_id="m",
                           workdir="ephemeral")
    mem = grx.memory

    # ---- ACT 1: what touched this entity? (cross-agent audit) ----------
    print("=" * 66)
    print("ACT 1  everything that touched order:1234")
    print("=" * 66)
    trail = [
        ("delegate", "triage the refund request for order 1234", "triage_agent"),
        ("delegate", "check delivery status of order 1234", "orders_desk"),
        ("delegate", "issue partial refund on order 1234", "refunds_desk"),
        ("delegate", "email the customer about order 1234", "comms_agent"),
    ]
    for dtype, task, agent in trail:
        d = mem.record_decision(decision_type=dtype, task=task, chosen=agent,
                                domain="support")
        mem.record_outcome(decision_id=d, ok=True, kind="delegate",
                           chosen=agent, domain="support", session_id="ops")
        mem.record_touch(d, "order:1234", role="entity", detail={"by": agent})

    print("audit query -> mem.touching('order:1234'):")
    for t in mem.touching("order:1234"):
        print(f"   - {t['by']:<13} touched order:1234   (decision {t['decision_id'][:16]}...)")

    # ---- ACT 2: which incident spawned this governing artifact? ---------
    print("\n" + "=" * 66)
    print("ACT 2  trace a live proposal back to the incident that caused it")
    print("=" * 66)

    @grx.healer                                   # stands in for hidden DSPy
    def _heal(tool, args, err):
        return graxella.TransformRecipe(field_map={"order_id": "order_ref"})

    def shipping_v2(a: dict) -> str:
        return f"in transit (ref {a['order_ref']})"

    @grx.tool(name="shipping_status", fallback=shipping_v2)
    def shipping_status(order_id: str) -> str:
        """delivery status by order id"""
        raise TypeError("unexpected keyword argument 'order_id'; schema "
                        "deprecated - use 'order_ref' instead")

    print("day 1: shipping_status drifts and self-heals ...")
    shipping_status.invoke({"order_id": "1234"})

    pid = grx.pending()[0]["proposal_id"]
    print(f"   a transform proposal is now pending review: {pid[:28]}...")
    origin = mem.touching(pid)                    # reverse-provenance
    print("\n   audit query -> who created this proposal?  mem.touching(proposal):")
    for o in origin:
        did = o["decision_id"]
        prov = mem.provenance(did)
        stmt = (prov["assertion"] or {}).get("statement", "")
        print(f"   - created by decision {did[:16]}...")
        print(f"     that decision was: {stmt}")
        print(f"     and it touched: {[t['target'] for t in prov['touched']]}")

    print("\nforward + backward provenance, one ledger: an artifact governing")
    print("production is traceable to the exact incident that spawned it.")


if __name__ == "__main__":
    main()
