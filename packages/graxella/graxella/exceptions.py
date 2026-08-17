"""Graxella exception hierarchy.

One base class so callers can `except GraxellaError` at the edge and
still let unrelated errors propagate. Subclasses are added when we grow
real behavior that switches on error type — no speculative hierarchy.
"""
from __future__ import annotations


class GraxellaError(Exception):
    """Base class for every Graxella-raised error."""


class RulebookError(GraxellaError):
    """Rulebook could not be loaded, promoted, or persisted."""


class ProposalNotFoundError(GraxellaError):
    """A `promote --id` (or MCP promote_proposal) referenced a proposal
    the current mine did not produce. The evidence probably moved."""


class UnsafeRuleError(GraxellaError):
    """A Datalog rule has head variables not bound by its body."""
