"""Tutorial 01 -- your first governed tool (2 minutes, no LLM needed).

graxella's promise: write plain Python, get governance for free.
One decorator turns a function into a tool whose every call is recorded
as evidence -- who was called, did it work, how long it took.

Run:  python tutorials/01_first_tool.py
"""
import sys
from pathlib import Path

# make graxella importable straight from the repo (skip if pip-installed)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "graxella"))

import graxella

# 1. A Session is the one object you hold: it owns the memory (ledger),
#    the rulebook, and the Evidence Gate for this project.
grx = graxella.Session("my-first-app", domain="tutorial", workdir="ephemeral")

# 2. Decorate a plain function. That's the whole integration.
#    You get back a real LangChain tool -- it drops into create_agent()
#    unchanged -- and every call now lands in the ledger.
@grx.tool
def check_order(order_id: str) -> str:
    """look up an order by its id"""
    return f"order {order_id}: shipped, arriving Tuesday"

# 3. Call it like any tool.
print(check_order.invoke({"order_id": "1234"}))
print(check_order.invoke({"order_id": "5678"}))

# 4. The governance you got for free: a typed outcome ledger.
stats = grx.stats()["total"]
print(f"\nledger: {stats['count']} calls recorded, ok_rate={stats['ok_rate']}")

# What you learned:
#   grx = graxella.Session(...)   -> one object, all governance
#   @grx.tool                     -> plain function becomes a governed tool
#   grx.stats()                   -> evidence, not vibes
#
# Next: 02_self_healing.py -- what happens when a tool's API drifts.
