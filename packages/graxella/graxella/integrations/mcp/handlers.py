"""MCP handler functions — pure Python, no MCP dependency.

Design split: the MCP binding (`graxella.integrations.mcp.server`) is optional
and only imports the `mcp` SDK when serving. The handler functions here are
plain functions that any transport (MCP stdio, HTTP, CLI, unit test) can call.

Handlers all take primitive kwargs and return JSON-serializable dicts —
that shape is what MCP tools expect, and it keeps handlers unit-testable
without any protocol machinery.

Handlers exposed (all read the underlying rulebook + store):

  * list_proposals(store_path, docs_path=None, rulebook_path=None)
      Combine the RuleDistiller / CapabilityReweigher / TrustPromoter output
      with (optionally) the DocsMiner output. Annotates each proposal with
      its rulebook status (pending / promoted / rejected).

  * promote_proposal(store_path, rulebook_path, proposal_id, reviewer, docs_path=None)
      Idempotent — re-promoting is a no-op. Returns the resulting ApprovedRule.

  * reject_proposal(rulebook_path, proposal_id)
      Marks the proposal id so `list_proposals` hides it by default.

  * list_rules(rulebook_path)
      All ApprovedRules in insertion order.

  * query_episodes(store_path, session=None, intent=None, limit=50)
      Simple filter over the store — returns Episode dicts.

  * query_seed(docs_path, query, k=5)
      TF-IDF-lite lookup over documentation assertions.

  * export_audit(store_path, rulebook_path, out_path, docs_path=None)
      Emit the PROV-O JSON-LD bundle to `out_path`; return the payload.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from graxella.agenda import DocsMiner, HiddenAgendaRunner
from graxella.agenda.miners import Proposal
from graxella.audit import export as audit_export
from graxella.knowledge import from_docs
from graxella.memory import SqliteExperienceStore
from graxella.rulebook import ApprovedRule, Rulebook


# --------------------------------------------------------------- primitives
def _open_store(path: str) -> SqliteExperienceStore:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"experience store not found: {p}")
    return SqliteExperienceStore(p)


def _mine_all(store: SqliteExperienceStore,
              docs_path: str | None,
              default_intent: str = "") -> list[Proposal]:
    proposals = HiddenAgendaRunner(store=store).run()
    if docs_path:
        seed = from_docs(docs_path)
        proposals.extend(DocsMiner(seed=seed,
                                   default_intent=default_intent).mine([]))
    return proposals


def _status(p: Proposal, rulebook: Rulebook | None) -> str:
    if rulebook is None:
        return "pending"
    if rulebook.is_rejected(p.id):
        return "rejected"
    for r in rulebook.all_rules():
        if r.proposal_id == p.id:
            return "promoted"
    return "pending"


def _rule_dict(r: ApprovedRule) -> dict[str, Any]:
    return r.to_dict()


def _episode_dict(e: Any) -> dict[str, Any]:
    return e.to_dict()


# --------------------------------------------------------------- handlers
def list_proposals(*, store_path: str,
                   docs_path: str | None = None,
                   rulebook_path: str | None = None,
                   include_rejected: bool = False) -> dict[str, Any]:
    """Return the current mine annotated with rulebook status."""
    store = _open_store(store_path)
    try:
        rb = Rulebook(path=Path(rulebook_path)) if rulebook_path else None
        proposals = _mine_all(store, docs_path)
        rows: list[dict[str, Any]] = []
        for p in proposals:
            status = _status(p, rb)
            if status == "rejected" and not include_rejected:
                continue
            row = p.to_dict()
            row["status"] = status
            rows.append(row)
        return {
            "episodes": len(store.all()),
            "docs_path": docs_path,
            "proposals": rows,
        }
    finally:
        store.close()


def promote_proposal(*, store_path: str, rulebook_path: str,
                     proposal_id: str, reviewer: str = "human",
                     docs_path: str | None = None) -> dict[str, Any]:
    """Promote by id. Requires the proposal to be present in the CURRENT mine —
    stale ids raise. Include `docs_path` so docs-derived proposals are eligible."""
    store = _open_store(store_path)
    try:
        rb = Rulebook(path=Path(rulebook_path))
        proposals = {p.id: p for p in _mine_all(store, docs_path)}
        target = proposals.get(proposal_id)
        if target is None:
            raise KeyError(f"proposal {proposal_id!r} not present in current mine")
        rule = rb.promote(target, approved_by=reviewer)
        return {"ok": True, "rule": _rule_dict(rule)}
    finally:
        store.close()


def reject_proposal(*, rulebook_path: str,
                    proposal_id: str) -> dict[str, Any]:
    rb = Rulebook(path=Path(rulebook_path))
    rb.reject(proposal_id)
    return {"ok": True, "rejected": proposal_id}


def list_rules(*, rulebook_path: str) -> dict[str, Any]:
    rb = Rulebook(path=Path(rulebook_path))
    return {"rules": [_rule_dict(r) for r in rb.all_rules()]}


def query_episodes(*, store_path: str,
                   session: str | None = None,
                   intent: str | None = None,
                   limit: int = 50) -> dict[str, Any]:
    store = _open_store(store_path)
    try:
        eps = store.all()
        if session:
            eps = [e for e in eps if e.session_id == session]
        if intent:
            eps = [e for e in eps if e.intent == intent]
        eps.sort(key=lambda e: e.started_at, reverse=True)
        return {
            "total_matched": len(eps),
            "episodes": [_episode_dict(e) for e in eps[:limit]],
        }
    finally:
        store.close()


def query_seed(*, docs_path: str, query: str,
               k: int = 5) -> dict[str, Any]:
    seed = from_docs(docs_path)
    matches = seed.find(query, k=k)
    return {
        "query": query,
        "matches": [a.to_dict() for a in matches],
        "stats": seed.stats(),
    }


def export_audit(*, store_path: str, rulebook_path: str, out_path: str,
                 docs_path: str | None = None) -> dict[str, Any]:
    """Emit PROV-O JSON-LD to `out_path`. Response includes a compact summary
    so the caller doesn't have to re-parse the file just to report success."""
    store = _open_store(store_path)
    try:
        rb = Rulebook(path=Path(rulebook_path))
        proposals = _mine_all(store, docs_path)
        payload = audit_export(store, rb, proposals, path=out_path)
        graph = payload.get("@graph", [])
        kinds: dict[str, int] = {}
        for node in graph:
            k = node.get("grx:kind", "?")
            kinds[k] = kinds.get(k, 0) + 1
        return {
            "ok": True,
            "path": out_path,
            "nodes": len(graph),
            "breakdown": kinds,
        }
    finally:
        store.close()


# --------------------------------------------------------------- registry
HANDLERS: dict[str, Any] = {
    "list_proposals":     list_proposals,
    "promote_proposal":   promote_proposal,
    "reject_proposal":    reject_proposal,
    "list_rules":         list_rules,
    "query_episodes":     query_episodes,
    "query_seed":         query_seed,
    "export_audit":       export_audit,
}
