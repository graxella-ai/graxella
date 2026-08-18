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
from graxella.exceptions import UnsafeRuleError
from graxella.gate import spec as _spec


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
    # Unified pipeline (task 1-5): the spec-Proposal lineage this rule
    # shipped through, and the citations its approval rests on.
    spec_status: str = ""
    citations: list[str] = field(default_factory=list)

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
            if rule.kind not in ("rule", "tool_binding", "transform") \
                    or rule.replace_skill != tool_name:
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
    def promote(self, proposal: Any, *,
                approved_by: str | None = "human",
                gate: Any = None,
                domain: str = "default") -> ApprovedRule:
        """Promote a proposal through the UNIFIED pipeline (task 1-5).

        Accepts a legacy agenda Proposal or a spec Proposal; either way,
        nothing lands in the rulebook without lineage:

          * a spec Proposal already APPROVED/ACTIVE ships as-is (its
            citations came from the Evidence Gate or a recorded human);
          * with ``gate=``, the Evidence Gate decides — AUTO_APPROVE ships,
            AUTO_REJECT raises, NEEDS_HUMAN ships only if ``approved_by``
            supplements it (operator sign-off is recorded as a citation);
          * with only ``approved_by``, the human decision IS the lineage,
            recorded as an operator citation;
          * with neither, UnsafeRuleError — there is no uncited path.

        Idempotent by proposal id — re-promoting is a no-op.
        """
        self.reload()
        spec_p = proposal if isinstance(proposal, _spec.Proposal) \
            else _spec.from_legacy(proposal, domain=domain)
        for existing in self._rules:
            if existing.proposal_id == spec_p.id:
                return existing

        spec_p = self._clear_gate(spec_p, gate=gate, approved_by=approved_by)

        change = dict(spec_p.payload or {})
        rule = ApprovedRule(
            id=f"apr_{spec_p.id.replace('prop_', '')}",
            proposal_id=spec_p.id,
            kind=(proposal.kind if not isinstance(proposal, _spec.Proposal)
                  else spec_p.kind.value),
            intent=str(change.get("if_intent") or change.get("intent") or ""),
            replace_skill=str(change.get("replace_skill")
                              or spec_p.target.tool or ""),
            with_skill=str(change.get("with_skill") or ""),
            recipe=_recipe_from_change(change),
            change=change,
            derived_from=[c.assertion_id for c in spec_p.evidence
                          if c.role is _spec.EvidenceRole.EPISODE],
            approved_at=time.time(),
            approved_by=spec_p.decided_by or approved_by or "gate:evidence",
            spec_status=spec_p.status.value,
            citations=[c.assertion_id for c in spec_p.evidence],
        )
        self._rules.append(rule)
        self._save()
        return rule

    @staticmethod
    def _clear_gate(spec_p: "_spec.Proposal", *, gate: Any,
                    approved_by: str | None) -> "_spec.Proposal":
        """Walk the proposal to ACTIVE — through the gate, the human, or
        both. Raises UnsafeRuleError when no lineage exists."""
        S = _spec.ProposalStatus
        if spec_p.status is S.ACTIVE:
            return spec_p
        if spec_p.status is S.APPROVED:
            return spec_p.with_status(S.ACTIVE, by=spec_p.decided_by or "promoter")

        if gate is not None:
            verdict, spec_p = gate.decide(spec_p)
            if spec_p.status is S.APPROVED:
                return spec_p.with_status(S.ACTIVE, by="gate:evidence")
            if spec_p.status is S.REJECTED:
                raise UnsafeRuleError(
                    f"gate rejected proposal {spec_p.id}: {verdict.reason}")
            # NEEDS_HUMAN falls through to the operator path below.

        if approved_by:
            op = _spec.EvidenceCitation(
                assertion_id=f"operator::{approved_by}",
                role=_spec.EvidenceRole.OPERATOR_DECISION,
                note="rulebook promotion sign-off",
            )
            if spec_p.status is S.PENDING:
                spec_p = spec_p.with_status(S.NEEDS_HUMAN,
                                            by=f"operator:{approved_by}")
            spec_p = spec_p.with_status(S.APPROVED, by=f"operator:{approved_by}",
                                        extra_evidence=(op,))
            return spec_p.with_status(S.ACTIVE, by=f"operator:{approved_by}")

        raise UnsafeRuleError(
            f"proposal {spec_p.id} has no promotion lineage: pass gate= "
            f"for an evidence decision, approved_by= for human sign-off, "
            f"or a proposal already APPROVED/ACTIVE")

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


def _recipe_from_change(change: dict[str, Any]) -> dict[str, Any]:
    """First-cut recipe extractor. Reviewers can attach a richer recipe by
    editing the rulebook file directly; the runtime honours whatever is there.

    Today: if the payload carries a field_map hint, use it; otherwise emit
    an empty recipe (a pure identity substitution — args get forwarded
    unchanged, which the target tool may or may not accept).
    """
    if "recipe" in change and isinstance(change["recipe"], dict):
        return dict(change["recipe"])
    if any(k in change for k in ("field_map", "static_defaults", "drop_fields")):
        return {"field_map": dict(change.get("field_map") or {}),
                "static_defaults": dict(change.get("static_defaults") or {}),
                "drop_fields": list(change.get("drop_fields") or ())}
    return {}
