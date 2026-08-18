"""Agenda CLI — the human-in-the-loop surface for the hidden agenda.

Six commands, one flow:

  graxella agenda run       --store PATH [--out proposals.json]
  graxella agenda review    --store PATH [--rulebook PATH]
  graxella agenda promote   --store PATH --rulebook PATH --id prop_xxx
                            [--as REVIEWER]
  graxella agenda reject    --rulebook PATH --id prop_xxx
  graxella agenda audit     --store PATH --rulebook PATH --out audit.jsonld
  graxella agenda rules     --rulebook PATH

Design notes:
  * No web UI in v0.1 — a CLI is the smallest reviewable surface and works
    fine for the "one person promotes rules" workflow. A web queue lands
    when there are multiple reviewers.
  * All commands are read-mostly except promote/reject, which are the ONLY
    ways a proposal reaches the live runtime. Everything is logged in the
    rulebook file for later audit export.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from graxella.agenda import HiddenAgendaRunner
from graxella.agenda.miners import Proposal
from graxella.audit import export as audit_export
from graxella.memory import SqliteExperienceStore
from graxella.rulebook import Rulebook


def _open_store(path: str) -> SqliteExperienceStore:
    p = Path(path)
    if not p.exists():
        print(f"error: store not found: {p}", file=sys.stderr)
        sys.exit(2)
    return SqliteExperienceStore(p)


def _open_rulebook(path: str) -> Rulebook:
    return Rulebook(path=Path(path))


def _mine(store: SqliteExperienceStore) -> list[Proposal]:
    return HiddenAgendaRunner(store=store).run()


def _fmt_proposal(p: Proposal, *, rulebook: Rulebook | None = None) -> str:
    tag = ""
    if rulebook is not None:
        if rulebook.is_rejected(p.id):
            tag = "  [REJECTED]"
        else:
            for r in rulebook.all_rules():
                if r.proposal_id == p.id:
                    tag = f"  [PROMOTED as {r.id}]"
                    break
    header = f"[{p.kind}] {p.subject}  id={p.id}  conf={p.confidence:.2f}{tag}"
    lines = [
        header,
        f"  evidence     : {p.evidence}",
        f"  derived_from : {p.derived_from}",
        f"  change       : {json.dumps(p.change, default=str)}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------- commands
def cmd_run(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    proposals = _mine(store)
    out = {
        "episodes": len(store.all()),
        "proposals": [p.to_dict() for p in proposals],
    }
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2, default=str))
        print(f"wrote {len(proposals)} proposals -> {args.out}")
    else:
        print(json.dumps(out, indent=2, default=str))
    store.close()
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    rulebook = _open_rulebook(args.rulebook) if args.rulebook else None
    proposals = _mine(store)
    if not proposals:
        print("no proposals — either no evidence yet, or nothing above threshold.")
        store.close()
        return 0
    print(f"=== {len(proposals)} proposal(s) from {len(store.all())} episode(s) ===\n")
    for p in proposals:
        if rulebook is not None and rulebook.is_rejected(p.id):
            continue
        print(_fmt_proposal(p, rulebook=rulebook))
        print()
    store.close()
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    rulebook = _open_rulebook(args.rulebook)
    proposals = {p.id: p for p in _mine(store)}
    target = proposals.get(args.id)
    if target is None:
        print(f"error: no proposal with id={args.id} in current mine "
              f"(store={args.store}). Did the evidence change?", file=sys.stderr)
        store.close()
        return 2
    rule = rulebook.promote(target, approved_by=args.reviewer)
    print(f"promoted {target.id} -> {rule.id}  ({rule.kind} / {rule.intent})")
    store.close()
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    rulebook = _open_rulebook(args.rulebook)
    rulebook.reject(args.id)
    print(f"rejected {args.id} — hidden from future reviews.")
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    rulebook = _open_rulebook(args.rulebook)
    rules = rulebook.all_rules()
    if not rules:
        print("(no approved rules yet)")
        return 0
    print(f"=== {len(rules)} approved rule(s) ===\n")
    for r in rules:
        print(f"[{r.kind}] {r.id}  approved_by={r.approved_by}")
        print(f"  intent        : {r.intent}")
        print(f"  replace_skill : {r.replace_skill}")
        print(f"  with_skill    : {r.with_skill}")
        print(f"  recipe        : {json.dumps(r.recipe, default=str)}")
        print(f"  derived_from  : {r.derived_from}")
        print()
    return 0


def cmd_tokens(args: argparse.Namespace) -> int:
    """Token/cost accounting from the outcome ledger alone (task 0A-3)."""
    from graxella.beliefs import Memory
    memory = Memory.sqlite(args.db, agent_id=args.agent, namespace=args.namespace)
    stats = memory.outcome_stats(domain=args.domain)
    total = stats["total"]
    print(f"outcomes: {total['count']}  ok: {total['ok']}  "
          f"ok_rate: {total['ok_rate']}")
    print(f"tokens_in: {total['tokens_in']}  tokens_out: {total['tokens_out']}  "
          f"cost_usd: {total['cost_usd']}")
    print(f"avg_latency_ms: {total['avg_latency_ms']}  "
          f"violations: {total['violations']}")
    if stats["by_domain"] and len(stats["by_domain"]) > 1:
        print("\nby domain:")
        for dom, agg in stats["by_domain"].items():
            print(f"  {dom}: n={agg['count']} ok_rate={agg['ok_rate']} "
                  f"tokens={agg['tokens_in']}+{agg['tokens_out']} "
                  f"avg_latency={agg['avg_latency_ms']}ms")
    return 0


def _gate_memory(args: argparse.Namespace):
    from graxella.beliefs import Memory
    return Memory.sqlite(args.db, agent_id=args.agent,
                         namespace=args.namespace)


def cmd_gate_list(args: argparse.Namespace) -> int:
    """Review queue from the ledger (task 1-8)."""
    from graxella.gate.evidence import pending_from_ledger
    rows = pending_from_ledger(_gate_memory(args))
    if not rows:
        print("no proposals awaiting review")
        return 0
    for r in rows:
        print(f"{r['proposal_id']}  [{r['kind']} @ {r['domain']}]  "
              f"posterior={r['posterior']}  n={r['evidence_n']}")
        print(f"    {r['reason']}")
    return 0


def cmd_gate_why(args: argparse.Namespace) -> int:
    from graxella.gate.evidence import EvidenceGate
    print(EvidenceGate(_gate_memory(args)).why(args.id))
    return 0


def cmd_gate_approve(args: argparse.Namespace) -> int:
    from graxella.gate.evidence import EvidenceGate
    aid = EvidenceGate(_gate_memory(args)).approve(
        args.id, by=args.reviewer, note=args.note or "")
    print(f"approved {args.id} by {args.reviewer}  (recorded: {aid})")
    print("the next decide() on this proposal folds this in")
    return 0


def cmd_gate_reject(args: argparse.Namespace) -> int:
    from graxella.gate.evidence import EvidenceGate
    aid = EvidenceGate(_gate_memory(args)).reject(
        args.id, by=args.reviewer, note=args.note or "")
    print(f"rejected {args.id} by {args.reviewer}  (recorded: {aid})")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    rulebook = _open_rulebook(args.rulebook)
    proposals = _mine(store)
    payload = audit_export(store, rulebook, proposals, path=args.out)
    n_nodes = len(payload.get("@graph", []))
    print(f"wrote PROV-O JSON-LD -> {args.out}  ({n_nodes} nodes)")
    store.close()
    return 0


# --------------------------------------------------------------- parser
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="graxella",
                                description="Graxella agenda CLI")
    sub = p.add_subparsers(dest="group", required=True)

    agenda = sub.add_parser("agenda", help="hidden-agenda review workflow")
    asub = agenda.add_subparsers(dest="cmd", required=True)

    r = asub.add_parser("run", help="mine the store and dump proposals")
    r.add_argument("--store", required=True)
    r.add_argument("--out", default=None,
                   help="write proposals JSON to this path (else stdout)")
    r.set_defaults(func=cmd_run)

    v = asub.add_parser("review", help="print proposals for human review")
    v.add_argument("--store", required=True)
    v.add_argument("--rulebook", default=None,
                   help="annotate with PROMOTED/REJECTED status if given")
    v.set_defaults(func=cmd_review)

    pr = asub.add_parser("promote", help="promote a proposal into the rulebook")
    pr.add_argument("--store", required=True)
    pr.add_argument("--rulebook", required=True)
    pr.add_argument("--id", required=True, help="proposal id (prop_xxx)")
    pr.add_argument("--as", dest="reviewer", default="human",
                    help="reviewer handle recorded in the rulebook")
    pr.set_defaults(func=cmd_promote)

    rj = asub.add_parser("reject", help="mark a proposal as rejected")
    rj.add_argument("--rulebook", required=True)
    rj.add_argument("--id", required=True, help="proposal id (prop_xxx)")
    rj.set_defaults(func=cmd_reject)

    ru = asub.add_parser("rules", help="list approved rules")
    ru.add_argument("--rulebook", required=True)
    ru.set_defaults(func=cmd_rules)

    au = asub.add_parser("audit", help="export PROV-O JSON-LD audit bundle")
    au.add_argument("--store", required=True)
    au.add_argument("--rulebook", required=True)
    au.add_argument("--out", required=True)
    au.set_defaults(func=cmd_audit)

    gate = sub.add_parser("gate", help="Evidence Gate review workflow")
    gsub = gate.add_subparsers(dest="cmd", required=True)
    for name, fn, extra in (
        ("list", cmd_gate_list, ()),
        ("why", cmd_gate_why, ("--id",)),
        ("approve", cmd_gate_approve, ("--id", "--as", "--note")),
        ("reject", cmd_gate_reject, ("--id", "--as", "--note")),
    ):
        g = gsub.add_parser(name)
        g.add_argument("--db", required=True, help="mnema sqlite db path")
        g.add_argument("--agent", default="graxella-mesh")
        g.add_argument("--namespace", default="default")
        if "--id" in extra:
            g.add_argument("--id", required=True, help="proposal id")
        if "--as" in extra:
            g.add_argument("--as", dest="reviewer", required=True,
                           help="reviewer handle recorded in the ledger")
        if "--note" in extra:
            g.add_argument("--note", default="")
        g.set_defaults(func=fn)

    tk = sub.add_parser("tokens",
                        help="token/cost accounting from the outcome ledger")
    tk.add_argument("--db", required=True, help="mnema sqlite db path")
    tk.add_argument("--agent", default="graxella-mesh",
                    help="agent_id the outcomes were recorded under")
    tk.add_argument("--namespace", default="default")
    tk.add_argument("--domain", default=None,
                    help="restrict the report to one evidence domain")
    tk.set_defaults(func=cmd_tokens)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
