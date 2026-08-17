"""Rulebook — approved graph mutations, on disk, hot-reloadable.

File format is intentionally simple JSON. Every entry retains a full
`derived_from` chain of Episode ids and the promoter's identity so any
runtime decision can be traced back to human evidence + human approval.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from graxella.agenda.miners import Proposal


@dataclass
class ApprovedRule:
    """One promoted proposal. Immutable once written."""
    id: str
    proposal_id: str
    kind: str                       # "rule" | "route_weight" | "trust_tier"
    intent: str
    replace_skill: str = ""
    with_skill: str = ""
    recipe: dict[str, Any] = field(default_factory=dict)   # healing.TransformRecipe shape
    change: dict[str, Any] = field(default_factory=dict)   # raw proposal change (for non-rule kinds)
    derived_from: list[str] = field(default_factory=list)
    approved_at: float = 0.0
    approved_by: str = "human"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Rulebook:
    """Approved rules on disk. Hot-reloadable via `reload()`.

    The runtime holds a Rulebook and consults it every call. Promotion
    appends to the file; the next `reload()` picks it up. For simplicity
    the demo reloads on every lookup — production would use a mtime check.
    """
    path: Path
    _rules: list[ApprovedRule] = field(default_factory=list)
    _rejected: set[str] = field(default_factory=set)
    _mtime: float = 0.0

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if self.path.exists():
            self._load()

    # -------------------------------------------------------------- lookups
    def find_substitution(self, tool_name: str,
                          intent: str | None = None) -> ApprovedRule | None:
        """Return the newest rule that substitutes `tool_name` under `intent`.

        Rules are checked newest-first so a later promotion supersedes an
        earlier one for the same (intent, tool_name) pair.
        """
        self.reload()
        for rule in reversed(self._rules):
            if rule.kind != "rule" or rule.replace_skill != tool_name:
                continue
            if intent is not None and rule.intent != intent:
                continue
            return rule
        return None

    def is_rejected(self, proposal_id: str) -> bool:
        self.reload()
        return proposal_id in self._rejected

    def all_rules(self) -> list[ApprovedRule]:
        self.reload()
        return list(self._rules)

    # ---------------------------------------------------------- mutations
    def promote(self, proposal: Proposal, *,
                approved_by: str = "human") -> ApprovedRule:
        """Promote a Proposal to an ApprovedRule and persist. Idempotent by
        proposal_id — re-promoting is a no-op."""
        self.reload()
        for existing in self._rules:
            if existing.proposal_id == proposal.id:
                return existing

        change = proposal.change or {}
        rule = ApprovedRule(
            id=f"apr_{proposal.id.replace('prop_', '')}",
            proposal_id=proposal.id,
            kind=proposal.kind,
            intent=str(change.get("if_intent") or change.get("intent") or ""),
            replace_skill=str(change.get("replace_skill") or ""),
            with_skill=str(change.get("with_skill") or ""),
            recipe=_recipe_from_evidence(proposal),
            change=dict(change),
            derived_from=list(proposal.derived_from),
            approved_at=time.time(),
            approved_by=approved_by,
        )
        self._rules.append(rule)
        self._save()
        return rule

    def reject(self, proposal_id: str) -> None:
        """Mark a proposal as rejected. Rejected proposals are hidden from
        `review` output on subsequent runs."""
        self.reload()
        self._rejected.add(proposal_id)
        self._save()

    # ------------------------------------------------------------ storage
    def reload(self) -> None:
        if not self.path.exists():
            return
        m = self.path.stat().st_mtime
        if m == self._mtime:
            return
        self._load()

    def _load(self) -> None:
        payload = json.loads(self.path.read_text())
        self._rules = [ApprovedRule(**r) for r in payload.get("rules", [])]
        self._rejected = set(payload.get("rejected", []))
        self._mtime = self.path.stat().st_mtime

    def _save(self) -> None:
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "rules": [r.to_dict() for r in self._rules],
            "rejected": sorted(self._rejected),
        }
        # Write atomically — a partial file breaks hot-reload.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str))
        tmp.replace(self.path)
        self._mtime = self.path.stat().st_mtime


def _recipe_from_evidence(proposal: Proposal) -> dict[str, Any]:
    """First-cut recipe extractor. Reviewers can attach a richer recipe by
    editing the rulebook file directly; the runtime honours whatever is there.

    Today: if the proposal carries a field_map hint in `change`, use it;
    otherwise emit an empty recipe (a pure identity substitution — args get
    forwarded unchanged, which the target tool may or may not accept).
    """
    change = proposal.change or {}
    if "recipe" in change and isinstance(change["recipe"], dict):
        return dict(change["recipe"])
    if "field_map" in change:
        return {"field_map": change["field_map"]}
    return {}
