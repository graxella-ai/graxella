"""Mnema: memory-grounded learning you can audit -- two mechanisms, no slogan.

"Agents that learn" is a hype phrase every framework claims. graxella's
differentiated claim is narrower and stronger: learning you can AUDIT and
REVERSE. This demo shows the two concrete mechanisms, both grounded in the
Mnema belief store, neither an RL/fine-tuning black box:

  ACT 1  recall     -- a differently-worded ticket benefits from a past one,
                       matched SEMANTICALLY by dense nomic-embed-text vectors
                       (not keyword/TF-IDF), and every recall is cited (why()).
  ACT 2  promotion  -- a drifted tool heals once (one DSPy call), a human
                       approves, and a FRESH session handles the same drift
                       with ZERO runtime llm. The agent learned the fix; the
                       belief is cited and can be retracted.

Requires a local Ollama with `qwen2.5:3b` and `nomic-embed-text`.
`pip install "graxella[heal]"` for the DSPy healer (falls back to Ollama).
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

# Path shim so the demo runs before graxella is pip-installed.
_PKG = Path(__file__).resolve().parents[1] / "packages" / "graxella"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

import graxella  # noqa: E402
from graxella.beliefs import best_embedder, embedder_id  # noqa: E402

logging.basicConfig(level=logging.ERROR, format="%(message)s")


def shipping_v2(args: dict) -> str:
    """New-schema endpoint: takes the POST-transform args (order_ref)."""
    return f"in transit (ref {args['order_ref']})"


def drifted_shipping(order_id: str) -> str:
    """get the delivery status of an order by its id"""
    # The upstream API migrated; the error hints the new field name.
    raise TypeError("unexpected keyword argument 'order_id'; schema "
                    "deprecated - use 'order_ref' instead")


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="mnema-learn-"))
    emb = best_embedder()                     # dense semantic if a model is up
    mem = graxella.Memory.sqlite(str(work / "mnema.db"), agent_id="support",
                                 namespace="support", embedder=emb)
    grx = graxella.Session("support-desk", domain="support",
                           model_id="qwen2.5:3b", memory=mem, workdir=work)
    print(f"recall embedder: {embedder_id(emb)} (dense semantic) "
          f"-- not keyword/TF-IDF\n")

    # ---- ACT 1: learning from verified experience (semantic recall) ----
    print("=" * 68)
    print("ACT 1  learning from verified experience -- semantic recall")
    print("=" * 68)

    def seed(task: str, chosen: str) -> None:
        aid = mem.record_decision(decision_type="delegate", task=task,
                                  chosen=chosen, domain="support")
        mem.record_outcome(decision_id=aid, ok=True, kind="delegate",
                           chosen=chosen, domain="support", session_id="seed")

    seed("customer cannot log in, the account is locked", "account_recovery")
    seed("refund requested for a damaged item that arrived broken", "refunds_desk")
    seed("where is my package for order 1234", "orders_desk")
    print("seeded 3 verified resolutions into Mnema.\n")

    for ticket in ["I'm locked out and can't sign in",
                   "my parcel still hasn't shown up"]:
        print(f"new ticket: {ticket!r}")
        cases = mem.similar_cases(ticket, top_k=1, domain="support")
        for c in cases:
            print(f"   recalled (sim={c.similarity}): {c.task!r}")
            print(f"   -> handled before by '{c.chosen}' (verified ok={c.ok}); "
                  f"reuse it")
        print("   (no prior experience)" if not cases else "")

    beliefs = mem.beliefs(predicate="decision")
    print(f"Mnema holds {len(beliefs)} decision beliefs + "
          f"{len(mem.beliefs(predicate='outcome'))} outcome beliefs (cited).")
    if beliefs:
        a = mem.why(beliefs[0]["id"]).get("assertion", {})
        print("why(a recalled belief):",
              {"statement": a.get("statement"),
               "confidence": a.get("confidence"), "status": a.get("status")})

    # ---- ACT 2: learning a PERMANENT fix (heal once -> approve -> 0 llm) -
    print("\n" + "=" * 68)
    print("ACT 2  learning a permanent fix -- promotion (gated, reversible)")
    print("=" * 68)

    grx.tool(drifted_shipping, name="shipping_status", fallback=shipping_v2)
    print("day 1: tool drifts for the first time ...")
    grx.tools["shipping_status"].invoke({"order_id": "1234"})
    print(f"   healed once via DSPy  (llm calls: {grx.healer_calls})")
    pend = grx.pending()
    print(f"   {len(pend)} proposal pending human review")
    grx.approve(pend[0], by="operator:lead", note="verified the v2 mapping")
    print("   human APPROVED -> promoted to a rule in the rulebook")

    # A fresh session (a new process tomorrow is identical): same workdir =
    # same ledger + rulebook, nothing cached in-process.
    print("\nday 2: a FRESH session, nothing cached in-process ...")
    grx2 = graxella.Session("support-desk", domain="support",
                            model_id="qwen2.5:3b", workdir=work)
    grx2.tool(drifted_shipping, name="shipping_status", fallback=shipping_v2)
    out = grx2.tools["shipping_status"].invoke({"order_id": "5678"})
    print(f"   result: {out}")
    print(f"   llm calls this session: {grx2.healer_calls}  "
          f"<- ZERO: the promoted rule handled the drift; the agent learned")

    assert grx2.healer_calls == 0, "expected zero-LLM handling via promoted rule"
    print("\nBoth kinds of learning are memory-grounded and auditable:")
    print("  - recall    : verified experience reused across paraphrases")
    print("  - promotion : a one-time heal became a permanent, cited, "
          "reversible rule -- zero runtime LLM thereafter")


if __name__ == "__main__":
    main()
