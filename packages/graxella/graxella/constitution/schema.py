"""graxella.constitution.schema — Constitution document + enforcement.

A Constitution declares four kinds of guardrail:

  * ``budgets``     — cost / latency / token ceilings per invocation
  * ``side_effects`` — which classes of side-effect the agent may perform
  * ``invariants``  — JSON-Schema predicates that must hold at every step
  * ``determinism`` — allowed drift from a canonical trajectory

Enforcement is **detection-only**. ``check_*`` methods return a list of
``Violation`` records; they never mutate the runtime or block a dispatch.
Auto-remediation would silently destroy the audit trail — the whole point
of a constitution is that violations are surfaced through the tracer for
human review.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, SchemaError


CONSTITUTION_META_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "graxella.Constitution",
    "type": "object",
    "properties": {
        "version": {"type": "string"},
        "budgets": {
            "type": "object",
            "properties": {
                "max_cost_usd_per_run": {"type": "number", "minimum": 0},
                "max_latency_ms_per_run": {"type": "integer", "minimum": 0},
                "max_tokens_per_run": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "side_effects": {
            "type": "object",
            "properties": {
                "allow": {"type": "array", "items": {"type": "string"}},
                "deny": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        "invariants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "applies_to": {"type": "string"},   # node name / decision_type
                    "predicate": {"type": "object"},     # JSON Schema fragment
                    "severity": {"type": "string", "enum": ["warning", "error"]},
                },
                "required": ["name", "predicate"],
            },
        },
        "determinism": {
            "type": "object",
            "properties": {
                "canonical_trajectory": {"type": "array", "items": {"type": "string"}},
                "max_edit_distance": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    },
    "required": ["version"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Violation:
    """A single constitution violation. Detection-only, never blocking."""

    kind: str          # "budget" | "side_effect" | "invariant" | "determinism"
    name: str          # budget key, invariant name, side-effect class, or "trajectory"
    detail: str        # human-readable one-liner
    severity: str = "error"     # "warning" | "error"
    applies_to: str | None = None
    subject: Any = None         # the input that failed
    expected: Any = None        # what the constitution required

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "name": self.name, "detail": self.detail,
            "severity": self.severity, "applies_to": self.applies_to,
            "subject": self.subject, "expected": self.expected,
        }


@dataclass
class _CompiledInvariant:
    """A JSON-Schema predicate ready to validate outputs."""

    name: str
    applies_to: str | None
    severity: str
    validator: Draft202012Validator


@dataclass
class Constitution:
    """A loaded, compiled constitution. Predicates are compiled at
    construction so ``check_*`` calls are O(size-of-instance)."""

    version: str
    document: dict = field(default_factory=dict)
    source_path: str | None = None
    _invariants: list[_CompiledInvariant] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compile_invariants()

    # -- constructors --------------------------------------------------------

    @classmethod
    def from_json(cls, path: str | Path) -> "Constitution":
        p = Path(path)
        doc = json.loads(p.read_text(encoding="utf-8"))
        cls._validate_shape(doc)
        return cls(version=doc.get("version", "0"), document=doc, source_path=str(p))

    @classmethod
    def from_dict(cls, doc: dict) -> "Constitution":
        cls._validate_shape(doc)
        return cls(version=doc.get("version", "0"), document=dict(doc))

    @classmethod
    def empty(cls) -> "Constitution":
        return cls(version="0", document={"version": "0"})

    # -- introspection -------------------------------------------------------

    def budgets(self) -> dict:
        return dict(self.document.get("budgets") or {})

    def side_effects(self) -> dict:
        return dict(self.document.get("side_effects") or {})

    def invariants(self) -> list[dict]:
        return list(self.document.get("invariants") or [])

    def determinism(self) -> dict:
        return dict(self.document.get("determinism") or {})

    # -- the four checks -----------------------------------------------------

    def check_budget(self, usage: dict) -> list[Violation]:
        """Compare a per-run usage dict against declared ceilings.

        ``usage`` keys: ``cost_usd``, ``latency_ms``, ``tokens``. Any key
        not declared in the constitution is ignored (no ceiling => no
        violation).
        """
        b = self.budgets()
        out: list[Violation] = []
        for key, ceiling_key in (
            ("cost_usd", "max_cost_usd_per_run"),
            ("latency_ms", "max_latency_ms_per_run"),
            ("tokens", "max_tokens_per_run"),
        ):
            ceiling = b.get(ceiling_key)
            actual = usage.get(key)
            if ceiling is None or actual is None:
                continue
            if actual > ceiling:
                out.append(Violation(
                    kind="budget", name=ceiling_key,
                    detail=f"{key}={actual} exceeds ceiling {ceiling}",
                    subject={key: actual}, expected={ceiling_key: ceiling},
                ))
        return out

    def check_side_effect(self, side_effect_class: str) -> list[Violation]:
        """A side-effect is permitted iff (allow is empty OR class in allow)
        AND class not in deny. Deny wins."""
        se = self.side_effects()
        allow = list(se.get("allow") or [])
        deny = list(se.get("deny") or [])
        if side_effect_class in deny:
            return [Violation(
                kind="side_effect", name=side_effect_class,
                detail=f"{side_effect_class!r} is in deny list",
                subject={"class": side_effect_class},
                expected={"deny": deny},
            )]
        if allow and side_effect_class not in allow:
            return [Violation(
                kind="side_effect", name=side_effect_class, severity="warning",
                detail=f"{side_effect_class!r} not in allow list",
                subject={"class": side_effect_class},
                expected={"allow": allow},
            )]
        return []

    def check_invariants(self, output: Any, *, applies_to: str | None = None) -> list[Violation]:
        """Validate ``output`` against every invariant whose ``applies_to``
        matches (or is unset — matches all)."""
        out: list[Violation] = []
        for inv in self._invariants:
            if inv.applies_to is not None and applies_to is not None \
                    and inv.applies_to != applies_to:
                continue
            errors = sorted(inv.validator.iter_errors(output), key=lambda e: e.path)
            for err in errors:
                path = "/".join(str(p) for p in err.absolute_path) or "$"
                out.append(Violation(
                    kind="invariant", name=inv.name, severity=inv.severity,
                    detail=f"at {path}: {err.message}",
                    applies_to=inv.applies_to,
                    subject=err.instance,
                    expected=err.schema,
                ))
        return out

    def check_determinism(self, trajectory: list[str]) -> list[Violation]:
        """Compare the actual trajectory against the canonical one under
        an edit-distance budget. Missing canonical => no check."""
        det = self.determinism()
        canonical = det.get("canonical_trajectory")
        budget = det.get("max_edit_distance")
        if canonical is None or budget is None:
            return []
        distance = _edit_distance(list(trajectory), list(canonical))
        if distance > budget:
            return [Violation(
                kind="determinism", name="trajectory",
                detail=f"edit_distance={distance} exceeds budget {budget}",
                subject=list(trajectory),
                expected={"canonical_trajectory": canonical, "max_edit_distance": budget},
            )]
        return []

    def check(self, *, decision: dict | None = None,
              output: Any = None,
              usage: dict | None = None,
              trajectory: list[str] | None = None,
              side_effect_class: str | None = None,
              applies_to: str | None = None) -> list[Violation]:
        """Convenience: run every applicable check in one call. Any argument
        left as None is skipped."""
        violations: list[Violation] = []
        if usage is not None:
            violations.extend(self.check_budget(usage))
        if side_effect_class is not None:
            violations.extend(self.check_side_effect(side_effect_class))
        if output is not None or applies_to is not None:
            violations.extend(self.check_invariants(output, applies_to=applies_to))
        if trajectory is not None:
            violations.extend(self.check_determinism(list(trajectory)))
        _ = decision  # accepted for API symmetry; used by callers for context
        return violations

    # -- internal ------------------------------------------------------------

    def _compile_invariants(self) -> None:
        self._invariants = []
        for inv in self.invariants():
            predicate = inv.get("predicate") or {}
            try:
                Draft202012Validator.check_schema(predicate)
            except SchemaError as e:
                raise ValueError(
                    f"invariant {inv.get('name')!r}: invalid JSON-Schema predicate: {e.message}"
                ) from e
            self._invariants.append(_CompiledInvariant(
                name=str(inv.get("name") or "unnamed"),
                applies_to=inv.get("applies_to"),
                severity=str(inv.get("severity") or "error"),
                validator=Draft202012Validator(predicate),
            ))

    @staticmethod
    def _validate_shape(doc: dict) -> None:
        try:
            Draft202012Validator(CONSTITUTION_META_SCHEMA).validate(doc)
        except Exception as e:
            raise ValueError(f"constitution shape violates meta-schema: {e}") from e


def _edit_distance(a: list[str], b: list[str]) -> int:
    """Levenshtein distance on token sequences. O(len(a)*len(b))."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ai in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, bj in enumerate(b, 1):
            cost = 0 if ai == bj else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]
