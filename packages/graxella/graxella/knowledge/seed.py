"""Assertion + KnowledgeSeed — the container ingest emits.

An Assertion is a typed (subject, predicate, object) triple with a source
citation and (optionally) an evidence sentence. Predicates are drawn from a
small controlled vocabulary so a downstream miner can pattern-match without
NLP:

  * deprecated_by     : old_name → new_name          (drives substitution rules)
  * field_maps_to     : old_field → new_field        (drives TransformRecipe field_map)
  * described_as      : tool_name → free-text        (feeds semantic tool catalog)
  * has_intent        : tool_name → intent_label     (documented intent binding)
  * has_arg           : tool_name → arg_name         (documented signature)

KnowledgeSeed indexes assertions by subject and by predicate so lookups are
O(1) at runtime. The `find(query)` surface uses the same TF-IDF-lite ranker
that ships with the tool catalog — one style, one dependency footprint.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

_WORD = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


# Controlled vocabulary — do not accept freeform predicates. The miner and
# runtime rely on this alphabet being small and stable.
KNOWN_PREDICATES: frozenset[str] = frozenset({
    "deprecated_by",
    "field_maps_to",
    "described_as",
    "has_intent",
    "has_arg",
})


@dataclass
class Assertion:
    """One typed fact extracted from a document.

    source_uri format: `file:///path/to/doc.md#L42` — enough for an auditor
    to open the exact line that produced the assertion.
    """
    subject: str
    predicate: str
    object: str
    source_uri: str = ""
    evidence: str = ""
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.predicate not in KNOWN_PREDICATES:
            raise ValueError(
                f"unknown predicate {self.predicate!r}. "
                f"Allowed: {sorted(KNOWN_PREDICATES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KnowledgeSeed:
    """Read-only view over a batch of Assertions.

    Constructed by `graxella.knowledge.from_docs(...)`; consumed by:
      * DocsMiner       — reads `deprecated_by` + `field_maps_to` and emits Proposals
      * Runtime lookups — `about(tool)` / `find(query)` return docs-backed context
    """
    assertions: list[Assertion] = field(default_factory=list)
    _by_subject: dict[str, list[Assertion]] = field(default_factory=dict, init=False, repr=False)
    _by_predicate: dict[str, list[Assertion]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._rebuild_indexes()

    def _rebuild_indexes(self) -> None:
        subj: dict[str, list[Assertion]] = defaultdict(list)
        pred: dict[str, list[Assertion]] = defaultdict(list)
        for a in self.assertions:
            subj[a.subject].append(a)
            pred[a.predicate].append(a)
        self._by_subject = dict(subj)
        self._by_predicate = dict(pred)

    # ----------------------------------------------------------- construction
    def extend(self, more: Iterable[Assertion]) -> None:
        self.assertions.extend(more)
        self._rebuild_indexes()

    # -------------------------------------------------------------- lookups
    def all(self) -> list[Assertion]:
        return list(self.assertions)

    def about(self, subject: str) -> list[Assertion]:
        return list(self._by_subject.get(subject, ()))

    def by_predicate(self, predicate: str) -> list[Assertion]:
        return list(self._by_predicate.get(predicate, ()))

    def deprecations(self) -> list[tuple[str, str, Assertion]]:
        """Return every (old, new, assertion) triple where old is deprecated by new.

        The Assertion is included so callers can cite the exact doc line that
        justified the migration — required for the audit trail.
        """
        return [(a.subject, a.object, a) for a in self.by_predicate("deprecated_by")]

    def field_map_for(self, old_name: str, new_name: str) -> dict[str, str]:
        """Assemble a {old_field: new_field} map for a specific migration.

        Walks `field_maps_to` assertions whose evidence names both tools —
        we scope by scanning `subject` prefix `old_name.field` if present,
        else fall back to any field_maps_to assertion tagged with both names
        in evidence.
        """
        out: dict[str, str] = {}
        for a in self.by_predicate("field_maps_to"):
            # Simple convention: subject is `old_name.field`, object is `new_name.field`.
            if a.subject.startswith(f"{old_name}.") and a.object.startswith(f"{new_name}."):
                out[a.subject.split(".", 1)[1]] = a.object.split(".", 1)[1]
        return out

    def find(self, query: str, k: int = 5) -> list[Assertion]:
        """Token-overlap ranking over subject + object + evidence."""
        q = _tokens(query)
        if not q:
            return self.assertions[:k]
        scored: list[tuple[float, Assertion]] = []
        for a in self.assertions:
            doc = f"{a.subject} {a.object} {a.evidence}"
            doc_tokens = _tokens(doc)
            hits = len(q & doc_tokens)
            if hits == 0:
                continue
            scored.append((hits / len(q), a))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [a for _score, a in scored[:k]]

    # --------------------------------------------------------------- diags
    def stats(self) -> dict[str, int]:
        out = {"total": len(self.assertions)}
        for pred, lst in self._by_predicate.items():
            out[pred] = len(lst)
        return out
