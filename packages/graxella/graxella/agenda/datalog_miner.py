"""DatalogMiner — derived substitutions via bottom-up rule evaluation.

Use case: single-hop deprecations in docs (`v1 -> v2`, `v2 -> v3`) should
imply `v1 -> v3`. DocsMiner emits only what a single Assertion says; this
miner adds a small Datalog program that CAN reason across assertions, then
re-emits the derived substitutions as Proposals for human review.

Design:
  * Takes a KnowledgeSeed + (optional) Rulebook + a Datalog Program.
  * The Program is expected to derive `substitute(Intent, Old, New)` facts.
  * Any derived substitute triple that isn't already an approved rule and
    isn't a base fact (i.e. is a genuinely new derivation) becomes a
    Proposal citing the transitive chain in its evidence.
  * Confidence for docs-derived transitive proposals is deliberately lower
    than DocsMiner's cap (0.4 vs 0.5) — a derived fact is one indirection
    away from the source citation.

Default program (used if caller passes no rules): captures the two most
common patterns the reviewer wants — transitive deprecation + intent
propagation. Copy-paste-extend the defaults for site-specific reasoning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from graxella.agenda.miners import Miner, Proposal, _proposal_id
from graxella.knowledge.seed import KnowledgeSeed
from graxella.memory.episode import ExperienceEpisode
from graxella.reasoning.datalog import (Atom, Program, Rule, Var, atom,
                                        evaluate, fact, var)
from graxella.reasoning.facts import rulebook_facts, seed_facts


def default_rules() -> list[Rule]:
    """A minimal, safe rulebook — copy + extend for site-specific logic.

    Predicates:
      * deprecated_by(Old, New)         — from docs
      * has_intent(Tool, Intent)        — from docs
      * substitute(Intent, Old, New)    — derived (or asserted by rulebook)

    Rules:
      1) direct: same as DocsMiner — but re-derived here for uniformity
         (idempotent — the miner filters against base facts before emitting).
      2) transitive: v1 -> v2 -> v3 implies v1 -> v3, carrying v3's intent.
      3) intent inheritance: if v2 -> v3 is documented and v3 has no intent,
         inherit v2's intent.
    """
    X, Y, Z, I = var("X"), var("Y"), var("Z"), var("I")
    return [
        # direct single-hop, intent from the successor
        Rule(head=atom("substitute", I, X, Y),
             body=[atom("deprecated_by", X, Y), atom("has_intent", Y, I)]),
        # transitive chain: any Z reachable through Y from X, with Z's intent
        Rule(head=atom("substitute", I, X, Z),
             body=[atom("deprecated_by", X, Y),
                   atom("deprecated_by", Y, Z),
                   atom("has_intent", Z, I)]),
        # intent inheritance: X inherits Y's intent when directly deprecated by Y
        Rule(head=atom("has_intent", X, I),
             body=[atom("deprecated_by", X, Y), atom("has_intent", Y, I)]),
    ]


@dataclass
class DatalogMiner(Miner):
    """Runs a Datalog program over Seed+Rulebook facts, emits derived
    substitutions as Proposals. Episodes are accepted for interface parity
    but not used — the miner reasons over facts, not lived state."""

    seed: KnowledgeSeed
    rulebook: Any | None = None                        # graxella.rulebook.Rulebook
    rules: list[Rule] = field(default_factory=default_rules)
    max_confidence: float = 0.4                        # <docs cap; derived facts are weaker
    name: str = field(default="datalog_miner", init=False)

    def mine(self, episodes: list[ExperienceEpisode]) -> list[Proposal]:  # noqa: ARG002
        program = Program(rules=list(self.rules))
        base_facts = seed_facts(self.seed)
        program.extend_facts(base_facts)
        if self.rulebook is not None:
            program.extend_facts(rulebook_facts(self.rulebook))

        closure = evaluate(program)
        derived_substitutes = closure.get("substitute", set())

        # Seed already-known substitutes so we only propose net-new triples.
        base_substitutes: set[tuple[str, str, str]] = set()
        for pred, args in base_facts:
            if pred == "substitute":
                base_substitutes.add(tuple(args))
        if self.rulebook is not None:
            for pred, args in rulebook_facts(self.rulebook):
                if pred == "substitute":
                    base_substitutes.add(tuple(args))

        proposals: list[Proposal] = []
        for triple in sorted(derived_substitutes):
            intent, old, new = triple
            if triple in base_substitutes:
                continue                                   # already known — nothing to propose

            # Reconstruct a chain for evidence — find intermediate hops if any.
            chain = _reconstruct_chain(closure, old, new)
            chain_str = " -> ".join(chain) if chain else f"{old} -> {new}"

            # Source citations: every Assertion whose (subject, object) touches
            # a hop in the chain. This keeps the audit trail resolvable back
            # to specific doc lines even for derived facts.
            derived_from = _cite_chain(self.seed, chain)

            subject = f"{intent}:{old}->{new}" if intent else f"{old}->{new}"
            change: dict[str, Any] = {
                "if_intent": intent,
                "replace_skill": old,
                "with_skill": new,
            }
            # First try the direct docs-asserted map. If none exists (typical
            # for derived transitive substitutions), compose hop-by-hop:
            # merging `hop[i].field_map_for(hop[i+1])` for every adjacent pair
            # yields the effective rename from the chain's endpoints. Missing
            # per-hop maps are treated as identity (fields pass through).
            fmap = self.seed.field_map_for(old, new)
            if not fmap and len(chain) >= 3:
                fmap = _compose_chain_field_map(self.seed, chain)
            if fmap:
                change["recipe"] = {"field_map": fmap}

            proposals.append(Proposal(
                id=_proposal_id("rule", subject),
                kind="rule",
                subject=subject,
                change=change,
                evidence=(f"datalog: derived chain {chain_str} "
                          f"(intent={intent!r})"),
                derived_from=derived_from,
                confidence=self.max_confidence,
            ))
        return proposals


# ------------------------------------------------------------------ helpers
def _reconstruct_chain(closure: dict[str, set], start: str, end: str) -> list[str]:
    """BFS over `deprecated_by` from `start` to `end` — returns the hop list
    (including both endpoints). Empty list if unreachable, which shouldn't
    happen if the closure derived (start,end)."""
    deprecated = closure.get("deprecated_by", set())
    # Build adjacency from ground facts only.
    adj: dict[str, list[str]] = {}
    for x, y in deprecated:
        adj.setdefault(x, []).append(y)

    from collections import deque
    q: deque[tuple[str, list[str]]] = deque([(start, [start])])
    seen = {start}
    while q:
        node, path = q.popleft()
        if node == end:
            return path
        for nxt in adj.get(node, ()):
            if nxt in seen:
                continue
            seen.add(nxt)
            q.append((nxt, path + [nxt]))
    return []


def _compose_chain_field_map(seed: KnowledgeSeed,
                             chain: list[str]) -> dict[str, str]:
    """Walk adjacent hops in `chain` and compose per-hop field_maps.

    Interpretation: for each hop (tool_a -> tool_b), `seed.field_map_for(a, b)`
    returns {old_field: new_field}. A field renamed at hop 1 must survive
    subsequent hops — if hop 2's map contains the renamed field as a key it
    gets renamed again; otherwise it passes through unchanged.

    Also carries through fields that AREN'T renamed at hop 1 but ARE renamed
    at hop 2 (they still need the hop-2 rename applied when the caller uses
    the endpoint tool).

    Returns the map from *original* first-hop field names to *final* endpoint
    field names, omitting entries where no rename ever happened.
    """
    # Start with hop 1's map. If it's empty, treat as {} — hop 2's map alone
    # then governs the composition.
    composed: dict[str, str] = dict(seed.field_map_for(chain[0], chain[1]))

    for a, b in zip(chain[1:], chain[2:]):
        hop_map = seed.field_map_for(a, b)
        if not hop_map:
            continue                                   # identity hop — nothing changes
        # Rewrite values that this hop renames.
        for k, v in list(composed.items()):
            if v in hop_map:
                composed[k] = hop_map[v]
        # Also add hop-map entries whose keys aren't already reachable
        # (fields unchanged at hop 1 but renamed here).
        touched_values = set(composed.values())
        for k, v in hop_map.items():
            if k not in touched_values and k not in composed:
                composed[k] = v

    # Drop identity renames — they clutter the recipe without changing behavior.
    return {k: v for k, v in composed.items() if k != v}


def _cite_chain(seed: KnowledgeSeed, chain: list[str]) -> list[str]:
    """Collect every source_uri from Assertions whose (subject, object)
    matches an adjacent pair in the chain — one Assertion per hop, plus
    the terminal tool's `has_intent` citation if present."""
    if len(chain) < 2:
        return []
    hops = list(zip(chain, chain[1:]))
    hop_set = set(hops)
    out: list[str] = []
    for a in seed.by_predicate("deprecated_by"):
        if (a.subject, a.object) in hop_set:
            if a.source_uri:
                out.append(a.source_uri)
    for a in seed.by_predicate("has_intent"):
        if a.subject == chain[-1] and a.source_uri:
            out.append(a.source_uri)
    return out
