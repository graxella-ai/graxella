"""Tiered recall -- keep breadth, spend the token budget on depth.

Adapted from the L0/L2 idea (OpenViking) and applied to Mnema recall: when
graxella injects "similar past tasks" into an agent's context, it doesn't
dump every case in full. The single most-relevant precedent renders in FULL
(L2); the rest render as one-line headlines (L0). Within the same character
budget the agent sees MORE precedents exist, and reads in detail only the one
that matters.

Honest note on magnitude: OpenViking's 34-91% token cut is for
document-sized content. graxella's recalled cases are compact, so the win is
smaller but real -- more precedents surfaced per budget, and the top case
shown in full instead of truncated. It grows as recall carries richer L2.

Requires Ollama with `nomic-embed-text` (semantic recall).
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "packages" / "graxella"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

import graxella  # noqa: E402
from graxella.beliefs import best_embedder, embedder_id  # noqa: E402
from graxella.beliefs.records import render_recall_block  # noqa: E402  (flat baseline)

logging.basicConfig(level=logging.ERROR, format="%(message)s")

SEED = [
    ("refund requested for a damaged item that arrived broken, buyer wants escalation", "refunds_desk"),
    ("customer says the parcel is late and has not been delivered after two weeks", "orders_desk"),
    ("buyer reports the product stopped working after a firmware update last night", "tech_support"),
    ("account is locked after several failed sign-in attempts, user cannot log in", "account_recovery"),
    ("chargeback opened by the bank for a transaction the customer does not recognise", "disputes_desk"),
    ("wrong size delivered, customer wants an exchange for the correct one", "returns_desk"),
    ("promo code did not apply at checkout and the customer was overcharged", "billing_desk"),
    ("customer asking whether the item can be gift-wrapped and shipped express", "orders_desk"),
]


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="tiered-recall-"))
    emb = best_embedder()
    mem = graxella.Memory.sqlite(str(work / "mnema.db"), agent_id="support",
                                 namespace="support", embedder=emb)
    print(f"recall embedder: {embedder_id(emb)} (semantic)\n")

    for task, chosen in SEED:
        aid = mem.record_decision(decision_type="delegate", task=task,
                                  chosen=chosen, domain="support")
        mem.record_outcome(decision_id=aid, ok=True, kind="delegate",
                           chosen=chosen, domain="support", session_id="seed")

    ticket = "my delivery never showed up and it has been weeks, where is my order"
    print(f"new ticket: {ticket!r}\n")

    cases = mem.similar_cases(ticket, top_k=8, domain="support")
    flat = render_recall_block(cases)                 # all at medium detail
    tiered = mem.recall(ticket, top_k=8, detail_k=1, domain="support")

    def precedents(block: str) -> int:
        return sum(1 for _, c in SEED if c in block)

    print("FLAT renderer  : %4d chars, %d precedents surfaced"
          % (len(flat), precedents(flat)))
    print("TIERED recall  : %4d chars, %d precedents surfaced  "
          "(top one in full, rest as headlines)"
          % (len(tiered), precedents(tiered)))
    print("\n--- what the agent actually sees (tiered) ---")
    print(tiered)


if __name__ == "__main__":
    main()
