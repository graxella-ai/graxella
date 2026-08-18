"""axon_fabric.interceptor — drift interception (Phase 2, tasks 2-4/2-5).

The B10 BrownBrillion port, made framework-agnostic and gate-governed.
``primary`` and ``fallback`` are plain callables ``dict -> Any`` — MCP
sessions, HTTP clients, LangChain tools all adapt in one lambda; the
fabric never depends on any framework or model.

The heal ladder, cheapest first ("fail once, learn forever"):

  1. happy path      — primary succeeds; typed outcome (kind="tool").
  2. promoted heal   — the Rulebook holds an ACTIVE, gate-approved
                       transform for this tool: apply it, call the
                       fallback. ZERO LLM. The outcome records under the
                       (domain, "transform", tool, model) tuple — every
                       promoted heal warms the gate for the next
                       proposal of the same shape.
  3. heal-once       — no promotion yet, a ``healer`` is configured:
                       the healer proposes a TransformRecipe ONCE (the
                       only step where an LLM may appear, and it exits
                       immediately into deterministic artifacts). If the
                       healed call succeeds, the recipe ships as a
                       Proposal(kind=transform) with a paired-replay
                       citation, decided by the Evidence Gate — cold
                       tuples land in the human review queue.
  4. loud failure    — nothing worked: typed failure outcome, re-raise.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Callable, Optional

from graxella.beliefs.adapter import Memory
from graxella.gate.audit import ReplayCase, audit, with_replay_evidence
from graxella.gate.evidence import EvidenceGate
from graxella.healing.recipes import TransformRecipe
from graxella.rulebook import Rulebook

#: B10's drift signature, kept verbatim — explicit upstream signals.
DRIFT_SIGNATURE = re.compile(
    r"HTTP_410_GONE|410\s+gone|schema\s+deprecated|capability\s+retired"
    r"|unknown\s+(?:field|argument|parameter)|unexpected\s+keyword",
    re.IGNORECASE,
)

#: A healer proposes a recipe from (tool_name, failed_args, error_text).
Healer = Callable[[str, dict, str], Optional[TransformRecipe]]


class ToolInterceptor:
    """Wrap one tool with the heal ladder. Call it like the tool."""

    def __init__(self, primary: Callable[[dict], Any], *,
                 tool_name: str,
                 memory: Memory,
                 rulebook: Rulebook,
                 gate: EvidenceGate | None = None,
                 fallback: Callable[[dict], Any] | None = None,
                 healer: Healer | None = None,
                 domain: str = "default",
                 model_id: str | None = None,
                 session_id: str | None = None) -> None:
        self.primary = primary
        self.tool_name = tool_name
        self.memory = memory
        self.rulebook = rulebook
        self.gate = gate or EvidenceGate(memory)
        self.fallback = fallback
        self.healer = healer
        self.domain = domain
        self.model_id = model_id
        self.session_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        self.healer_calls = 0   # observability: LLM invocations, ever
        # Rung 2.5: the recipe proposed at heal-once, reused deterministically
        # while its proposal awaits review — the LLM truly fires ONCE.
        self._proposed_recipe: TransformRecipe | None = None

    def __call__(self, args: dict) -> Any:
        aid = self.memory.record_decision(
            decision_type="tool", task=f"call {self.tool_name}",
            chosen=self.tool_name, domain=self.domain,
            model_id=self.model_id)
        try:
            result = self.primary(dict(args))
        except Exception as exc:
            if not is_drift(str(exc)):
                self._outcome(aid, ok=False, kind="tool", err=str(exc))
                raise
            return self._heal(aid, args, str(exc))
        self._outcome(aid, ok=True, kind="tool")
        return result

    # -- the heal ladder ------------------------------------------------------

    def _heal(self, aid: str, args: dict, error: str) -> Any:
        if self.fallback is None:
            self._outcome(aid, ok=False, kind="tool", err=error)
            raise RuntimeError(
                f"{self.tool_name} drifted and no fallback is configured: "
                f"{error}")

        # Rung 2: a promoted, gate-approved transform. Deterministic.
        rule = self.rulebook.find_substitution(self.tool_name)
        if rule is not None and rule.recipe:
            recipe = TransformRecipe(
                field_map=dict(rule.recipe.get("field_map") or {}),
                static_defaults=dict(rule.recipe.get("static_defaults") or {}),
                drop_fields=tuple(rule.recipe.get("drop_fields") or ()),
            )
            result = self.fallback(recipe.apply(dict(args)))
            self._outcome(aid, ok=True, kind="transform")
            return result

        # Rung 2.5: a recipe was already proposed this process and is
        # awaiting review — reuse it deterministically, zero LLM.
        if self._proposed_recipe is not None:
            result = self.fallback(self._proposed_recipe.apply(dict(args)))
            self._outcome(aid, ok=True, kind="transform")
            return result

        # Rung 3: heal once, then propose — never heal silently twice.
        if self.healer is not None:
            self.healer_calls += 1
            recipe = self.healer(self.tool_name, dict(args), error)
            if recipe is not None:
                self._proposed_recipe = recipe
                healed_args = recipe.apply(dict(args))
                result = self.fallback(healed_args)   # raises = rung 4
                self._outcome(aid, ok=True, kind="transform")
                proposal = recipe.to_proposal(
                    domain=self.domain, tool=self.tool_name,
                    origin="healer:axon-fabric", model_id=self.model_id)
                report = audit(proposal, [ReplayCase(
                    case_id=f"live::{aid}", inputs=dict(args),
                    expected=healed_args, source_ids=(aid,))],
                    memory=self.memory)
                proposal = with_replay_evidence(proposal, report)
                self.gate.refresh()
                self.gate.decide(proposal)   # cold -> human review queue
                return result

        self._outcome(aid, ok=False, kind="tool", err=error)
        raise RuntimeError(
            f"{self.tool_name} drifted; no promoted transform and no "
            f"healer produced one: {error}")

    def _outcome(self, aid: str, *, ok: bool, kind: str,
                 err: str | None = None) -> None:
        self.memory.record_outcome(
            decision_id=aid, ok=ok, kind=kind, err=err,
            err_class="drift" if (err and is_drift(err)) else None,
            domain=self.domain, chosen=self.tool_name,
            model_id=self.model_id, session_id=self.session_id)


def is_drift(message: str) -> bool:
    return bool(DRIFT_SIGNATURE.search(message))


__all__ = ["ToolInterceptor", "Healer", "is_drift", "DRIFT_SIGNATURE"]
