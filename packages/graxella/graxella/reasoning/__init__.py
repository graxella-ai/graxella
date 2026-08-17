"""graxella.reasoning — declarative rule backend.

The Rulebook stores approved substitutions as flat records — great for O(1)
lookup at dispatch time, weak at expressing *derived* logic like transitive
deprecation chains (`v1 -> v2 -> v3` should imply `v1 -> v3`).

This module adds a tiny bottom-up Datalog engine so a reviewer can encode
that logic once, evaluate against the current KnowledgeSeed + Rulebook, and
promote the resulting Proposals through the same audit path as any other
miner.

Public surface:
    Var, Atom, Rule, Program        — the Datalog language pieces
    evaluate(program) -> facts      — semi-naive fixpoint
    query(program, atom) -> [bind]  — pattern-based extraction
    seed_facts(seed)                — KnowledgeSeed -> ground facts
    rulebook_facts(rulebook)        — Rulebook -> ground facts

DatalogMiner lives under graxella.agenda alongside the other miners.
"""
from graxella.reasoning.datalog import (Atom, Program, Rule, Var,
                                        atom, evaluate, fact, query, var)
from graxella.reasoning.facts import rulebook_facts, seed_facts

__all__ = [
    "Var", "Atom", "Rule", "Program",
    "var", "atom", "fact",
    "evaluate", "query",
    "seed_facts", "rulebook_facts",
]
