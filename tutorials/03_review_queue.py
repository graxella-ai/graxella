"""Tutorial 03 -- the review queue: making a fix permanent (3 min, no LLM).

Tutorial 02 healed a drift and left a proposal in the review queue. Here
you play the operator: inspect WHY the gate wants review, approve the fix,
and watch a brand-new session handle the same drift with ZERO healer runs
-- the fix is now a promoted rule in the rulebook.

Run:  python tutorials/03_review_queue.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "graxella"))

import graxella

# A real (non-ephemeral) workdir so day-2 can find day-1's rulebook.
work = Path(tempfile.mkdtemp(prefix="graxella-tut03-"))


from pydantic import BaseModel


class ShipmentQuery(BaseModel):            # the carrier's v2 request schema
    order_ref: str


def shipping_v2(args: dict) -> str:
    q = ShipmentQuery(**args)              # genuine validation, not a prop
    return f"in transit (ref {q.order_ref})"


def make_session() -> graxella.Session:
    grx = graxella.Session("review-demo", domain="tutorial", workdir=work)

    @grx.healer
    def fix(tool_name, args, error):
        if "order_ref" in error:           # pydantic names the new field
            return graxella.TransformRecipe(field_map={"order_id": "order_ref"})
        return None

    @grx.tool(name="shipping_status", fallback=shipping_v2)
    def shipping_status(order_id: str) -> str:
        """delivery status of an order"""
        return shipping_v2({"order_id": order_id})   # drifts for real
    return grx


# ---- day 1: drift, heal, review, approve --------------------------------
grx = make_session()
grx.tools["shipping_status"].invoke({"order_id": "1234"})   # drift -> heal once

pending = grx.pending()
print(f"review queue: {len(pending)} proposal")
p = pending[0]
print("\nwhy does the gate want a human? ->")
print(grx.why(p))            # the cited verdict: posterior, threshold, evidence

grx.approve(p, by="operator:you", note="verified the field rename")
print("\napproved -> promoted to the rulebook.")

# ---- day 2: a FRESH session (same project folder) -----------------------
grx2 = make_session()        # new process tomorrow would look identical
out = grx2.tools["shipping_status"].invoke({"order_id": "9999"})
print(f"\nday 2, same drift: {out}")
print(f"healer runs in the new session: {grx2.healer_calls}   <- ZERO: "
      "the promoted rule handled it")

# What you learned:
#   grx.pending()        -> what awaits a human
#   grx.why(p)           -> the gate's cited reasoning (evidence, not opinion)
#   grx.approve(p, by=)  -> human decision, recorded forever, then promoted
#   promoted rules survive restarts -- the agent LEARNED the fix
#
# Next: 04_agent_mesh.py -- multiple agents, routed without a routing LLM.
