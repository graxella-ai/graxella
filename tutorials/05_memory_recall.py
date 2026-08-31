"""Tutorial 05 -- memory that recalls what worked (3 min).

graxella records every decision and its verified outcome. Later, a new,
DIFFERENTLY-WORDED task recalls the closest past cases -- so the agent
walks in with experience instead of starting cold.

Works out of the box. If a local Ollama with `nomic-embed-text` is
running, recall is dense-semantic (paraphrases match with zero shared
keywords); otherwise it falls back to a lexical embedder automatically.

Run:  python tutorials/05_memory_recall.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "graxella"))

import graxella
from graxella.beliefs import best_embedder, embedder_id

emb = best_embedder()          # richest available: semantic if Ollama is up
print(f"embedder: {embedder_id(emb)}\n")

grx = graxella.Session("recall-demo", domain="support", workdir="ephemeral",
                       memory=graxella.Memory.sqlite(
                           ":memory:", agent_id="recall-demo",
                           namespace="support", embedder=emb))
mem = grx.memory

# Seed three VERIFIED experiences (decision + recorded outcome). In a live
# app the mesh writes these automatically on every dispatch.
for task, agent in [
    ("customer cannot log in, the account is locked", "account_recovery"),
    ("refund requested for an item that arrived broken", "refunds_desk"),
    ("where is my package for order 1234", "orders_desk"),
]:
    aid = mem.record_decision(decision_type="delegate", task=task,
                              chosen=agent, domain="support")
    mem.record_outcome(decision_id=aid, ok=True, kind="delegate",
                       chosen=agent, domain="support", session_id="seed")

# A new ticket, worded differently from anything seen before:
ticket = "my parcel still hasn't shown up"
print(f"new ticket: {ticket!r}\n")

for c in mem.similar_cases(ticket, top_k=2, domain="support"):
    print(f"  recalled (sim={c.similarity}): {c.task!r}")
    print(f"    -> was handled by '{c.chosen}' (verified ok={c.ok})")

# The one-call version, tiered and budget-bounded, ready to inject as
# context: the best case in full, the rest as one-line headlines.
print("\nwhat the agent would see (mem.recall):\n")
print(mem.recall(ticket, top_k=3, domain="support"))

# What you learned:
#   outcomes recorded once     -> experience recalled forever
#   recall serves only VERIFIED cases (decisions with real outcomes)
#   mem.recall(...)            -> tiered context block, one call
#
# Next: 06_audit_trail.py -- answering "why?" and "what touched this?"
