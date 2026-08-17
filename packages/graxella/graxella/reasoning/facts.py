"""Bridge — turn Graxella's runtime objects into Datalog ground facts.

Two converters, both one-shot (call once, feed the tuples into a Program):

  * seed_facts(seed)      — every KnowledgeSeed Assertion becomes a fact
  * rulebook_facts(rb)    — every ApprovedRule becomes a `substitute/3` fact
                            plus provenance `approved_rule/1`.

Predicate schema — this alphabet is what DatalogMiner and any user-written
rule program can rely on:

    deprecated_by(Old, New)                 — Assertion(deprecated_by, ...)
    field_maps_to(OldField, NewField)       — full-qualified names, e.g.
                                              "get_weather.city" -> "fetch_forecast.location"
    described_as(Tool, Description)         — free-text description
    has_intent(Tool, Intent)
    has_arg(Tool, Arg)
    substitute(Intent, Old, New)            — already-approved substitutions
    approved_rule(RuleId)                   — provenance sentinel

Ground values are all strings — Datalog doesn't care about types, but keeping
one shape keeps unification predictable.
"""
from __future__ import annotations

from typing import Iterable

from graxella.reasoning.datalog import Atom, fact


def seed_facts(seed: "KnowledgeSeed") -> list[Atom]:  # noqa: F821 — TYPE_CHECKING avoided
    """Every Assertion becomes one Datalog fact with the same predicate name."""
    out: list[Atom] = []
    for a in seed.all():
        # Predicates in the seed's controlled vocab map 1-to-1 to Datalog
        # predicate names — the miner reads them by that name.
        out.append(fact(a.predicate, str(a.subject), str(a.object)))
    return out


def rulebook_facts(rulebook: "Rulebook") -> list[Atom]:  # noqa: F821
    """Approved rules become both substitute/3 (for the miner's rule head to
    detect already-approved chains) and approved_rule/1 (audit sentinel)."""
    out: list[Atom] = []
    for r in rulebook.all_rules():
        if r.kind != "rule" or not r.replace_skill or not r.with_skill:
            continue
        intent = r.intent or ""
        out.append(fact("substitute", intent, r.replace_skill, r.with_skill))
        out.append(fact("approved_rule", r.id))
    return out


def facts_from(*sources: Iterable[Atom]) -> list[Atom]:
    """Small convenience for callers that want to chain multiple sources
    without a temp list. Order is preserved; duplicates are NOT deduped
    (Program.extend_facts uses a set internally, so duplicates collapse there)."""
    out: list[Atom] = []
    for src in sources:
        out.extend(src)
    return out
