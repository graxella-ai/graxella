"""Tutorial 02 -- self-healing when an API drifts (3 minutes, no LLM needed).

The scenario every agent team hits: an upstream API renames a field and
your tool starts throwing. graxella's answer is the heal ladder:

    happy path -> promoted fix -> heal ONCE -> loud failure

The repair is proposed once, cached as a deterministic recipe, and queued
for review. It is never silently retried in a loop.

The drift here is REAL: the carrier's v2 client validates requests with
pydantic, so the failure is a genuine ValidationError from the library --
no error string was written to please graxella's detector. (Detection
covers signature drift, validation-library rejections, and HTTP 410 /
moved-endpoint 404s; auth errors and timeouts stay loud failures.)

This tutorial uses an explicit tiny healer so it runs anywhere. In real
use you can delete it: with a local Ollama, graxella ships a built-in
healer (DSPy under the hood) that does this automatically -- you write
only the tool and the fallback.

Run:  python tutorials/02_self_healing.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "graxella"))

import graxella

grx = graxella.Session("healing-demo", domain="tutorial", workdir="ephemeral")

# A tiny healer: from (tool, failed args, error text) propose a recipe.
# Delete this block if you run Ollama -- the built-in healer replaces it.
@grx.healer
def fix_renamed_field(tool_name, args, error):
    if "order_ref" in error:                       # the error names the new field
        return graxella.TransformRecipe(field_map={"order_id": "order_ref"})
    return None

# The carrier's NEW api. Note: nothing below hand-writes an error string
# for graxella to find -- the drift is a GENUINE pydantic ValidationError
# raised by the carrier's own request schema, exactly what a real client
# library throws after a migration.
from pydantic import BaseModel

class ShipmentQuery(BaseModel):        # the carrier's v2 request schema
    order_ref: str

def shipping_v2(args: dict) -> str:
    q = ShipmentQuery(**args)          # validates -- old args fail HERE
    return f"in transit (ref {q.order_ref})"

# Your tool, written against the OLD api -- it now drifts for real.
@grx.tool(fallback=shipping_v2)
def get_shipping_status(order_id: str) -> str:
    """delivery status of an order"""
    return shipping_v2({"order_id": order_id})

print("call 1 (tool drifts, graxella heals it once):")
print("  ", get_shipping_status.invoke({"order_id": "1234"}))

print("call 2 (cached recipe reused -- the healer does NOT run again):")
print("  ", get_shipping_status.invoke({"order_id": "5678"}))

print(f"\nhealer invocations, ever: {grx.healer_calls}   <- fail once, learn forever")
print(f"proposals awaiting review: {len(grx.pending())}   <- nothing is promoted silently")

# What you learned:
#   @grx.tool(fallback=...)  -> the heal ladder, no extra code
#   the repair runs ONCE; after that it is a deterministic recipe
#   every heal becomes a reviewable proposal -- governance, not magic
#
# Next: 03_review_queue.py -- approving a fix so it becomes permanent.
