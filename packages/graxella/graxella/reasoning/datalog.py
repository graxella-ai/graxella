"""Minimal bottom-up Datalog engine — pure Python, zero deps.

Scope on purpose:
  * Function-free Horn clauses (safe rules — every head variable must appear
    in the body). No negation, no aggregation, no arithmetic. Recursion is
    fine — the fixpoint terminates because the Herbrand base is finite.
  * Semi-naive evaluation. For the rulebook sizes we're mining (dozens of
    tools, hundreds of assertions) plain naive would be fine; semi-naive is
    kept for correctness at scale.
  * Ground-fact facts + head-only rules. No conjunctive queries at the
    surface — callers query one atom at a time; `evaluate()` returns the
    full closure if they need everything.

Why: the Rulebook stores flat substitutions. Transitive chains
(v1 -> v2 -> v3 implies v1 -> v3) require deriving new facts from
existing ones — that's Datalog's job. Compile-time evaluation, human
review of the derived substitutions, then promotion through the same
audit path.

Language primitives:
    Var("X")                 — a variable (Datalog convention: uppercase name,
                               but we don't enforce it — Var("x") works too)
    (pred, (arg, arg, ...))  — an atom; args are either Var or ground values
    Rule(head, body)         — head :- body[0], body[1], ...
    Program(rules, facts)    — facts is {pred: set[tuple]} of ground tuples

Sugar helpers:
    var("X")                 — Var("X")
    atom("pred", "a", Var("X"))    — ("pred", ("a", Var("X")))
    fact("pred", "a", "b")   — same shape, all ground

Safety rule: every variable in the head must appear in the body. `Rule`
validates this in `__post_init__` — an unsafe rule raises immediately.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator


# ------------------------------------------------------------------ terms
@dataclass(frozen=True)
class Var:
    """Datalog variable. Two Vars are equal iff their names match."""
    name: str

    def __repr__(self) -> str:  # pragma: no cover
        return f"?{self.name}"


Term = Any                # in practice: Var | str | int | float | tuple
Atom = tuple[str, tuple[Term, ...]]        # (predicate, args)
Binding = dict[str, Term]                  # variable name -> ground term


# --------------------------------------------------------------- sugar
def var(name: str) -> Var:
    return Var(name)


def atom(pred: str, *args: Term) -> Atom:
    return (pred, tuple(args))


def fact(pred: str, *args: Term) -> Atom:
    """Convenience — same shape as atom(), but the intent is 'this is ground'.
    Kept separate to make call sites self-documenting."""
    for a in args:
        if isinstance(a, Var):
            raise ValueError(f"fact({pred!r}, ...) got a Var {a!r} — facts must be ground")
    return (pred, tuple(args))


# --------------------------------------------------------------- rule
@dataclass
class Rule:
    head: Atom
    body: list[Atom] = field(default_factory=list)

    def __post_init__(self) -> None:
        head_vars = {a.name for a in self.head[1] if isinstance(a, Var)}
        body_vars: set[str] = set()
        for _p, args in self.body:
            body_vars.update(a.name for a in args if isinstance(a, Var))
        unsafe = head_vars - body_vars
        if unsafe:
            raise ValueError(
                f"unsafe rule: head variables {sorted(unsafe)} "
                f"not bound by body (rule head={self.head!r})"
            )

    def __repr__(self) -> str:  # pragma: no cover
        body = ", ".join(f"{p}({', '.join(map(repr, a))})" for p, a in self.body)
        pred, args = self.head
        return f"{pred}({', '.join(map(repr, args))}) :- {body}"


# --------------------------------------------------------------- program
@dataclass
class Program:
    """Rules + starting facts. Immutable inputs; `evaluate` copies internally."""
    rules: list[Rule] = field(default_factory=list)
    facts: dict[str, set[tuple[Term, ...]]] = field(default_factory=dict)

    def add_fact(self, a: Atom) -> None:
        pred, args = a
        self.facts.setdefault(pred, set()).add(tuple(args))

    def extend_facts(self, items: Iterable[Atom]) -> None:
        for a in items:
            self.add_fact(a)


# --------------------------------------------------------------- unify
def unify(pattern: tuple[Term, ...],
          ground: tuple[Term, ...],
          binding: Binding) -> Binding | None:
    """Match `pattern` (may contain Vars) against a ground tuple.

    Extends `binding` in a copy; returns None on conflict. Preserves the
    invariant that ground positions in the pattern must equal the fact's
    corresponding position.
    """
    if len(pattern) != len(ground):
        return None
    b = dict(binding)
    for p, g in zip(pattern, ground):
        if isinstance(p, Var):
            if p.name in b:
                if b[p.name] != g:
                    return None
            else:
                b[p.name] = g
        else:
            if p != g:
                return None
    return b


def _instantiate(a: Atom, binding: Binding) -> tuple[Term, ...]:
    _pred, args = a
    return tuple(binding[t.name] if isinstance(t, Var) else t for t in args)


# --------------------------------------------------------------- eval body
def _match_body(body: list[Atom],
                facts: dict[str, set[tuple[Term, ...]]],
                delta: dict[str, set[tuple[Term, ...]]] | None,
                binding: Binding) -> Iterator[Binding]:
    """Depth-first bind of `body` against `facts`.

    When `delta` is given (semi-naive round), at least one atom in the body
    must match against a fact that was newly derived in the previous
    iteration — that's what makes the round produce *only* new derivations,
    avoiding redundant re-derivations of the whole closure.
    """
    if not body:
        yield binding
        return

    for i, subgoal in enumerate(body):
        # We fan out per subgoal choice for the "at least one delta" clause.
        # For the non-delta case this loop degenerates to a single ordering
        # (i=0) — kept unified for readability.
        if delta is None and i > 0:
            break

        rest = body[:i] + body[i + 1:]
        pred, args = subgoal
        candidates = facts.get(pred, ())
        if delta is not None:
            candidates = delta.get(pred, ())
        # Rest of the body may match against the full fact base.
        rest_facts = facts

        for ground in candidates:
            b2 = unify(args, ground, binding)
            if b2 is None:
                continue
            for b3 in _match_body(rest, rest_facts, None, b2):
                yield b3


# --------------------------------------------------------------- evaluate
def evaluate(program: Program) -> dict[str, set[tuple[Term, ...]]]:
    """Semi-naive bottom-up fixpoint. Returns the full closure as a dict.

    Semantics: `program.facts` is treated as the seed EDB (extensional
    database). Each round evaluates every rule; a derived atom that isn't
    already in the closure joins it. The loop stops when a round produces
    no new atoms. Termination is guaranteed because Datalog has no function
    symbols — the Herbrand base is finite.
    """
    closure: dict[str, set[tuple[Term, ...]]] = {
        p: set(v) for p, v in program.facts.items()
    }
    # First round is naive to seed the deltas.
    delta: dict[str, set[tuple[Term, ...]]] = copy.deepcopy(closure)

    while delta:
        # Snapshot the round so `_match_body` sees a stable view — otherwise
        # additions below would mutate the set we're iterating.
        round_facts = {p: frozenset(v) for p, v in closure.items()}
        round_delta = {p: frozenset(v) for p, v in delta.items()}
        new_delta: dict[str, set[tuple[Term, ...]]] = {}
        for rule in program.rules:
            for binding in _match_body(rule.body, round_facts, round_delta, {}):
                head_pred, _ = rule.head
                head_ground = _instantiate(rule.head, binding)
                if head_ground in closure.get(head_pred, set()):
                    continue
                closure.setdefault(head_pred, set()).add(head_ground)
                new_delta.setdefault(head_pred, set()).add(head_ground)
        delta = new_delta

    return closure


# --------------------------------------------------------------- query
def query(program: Program, pattern: Atom) -> list[Binding]:
    """Return every binding that makes `pattern` true in the closure.

    Convenience for callers who want "give me all X such that
    substitute(weather, get_weather, X)" without hand-walking the closure.
    """
    closure = evaluate(program)
    pred, args = pattern
    out: list[Binding] = []
    for ground in sorted(closure.get(pred, set()), key=lambda t: tuple(map(str, t))):
        b = unify(args, ground, {})
        if b is not None:
            out.append(b)
    return out
