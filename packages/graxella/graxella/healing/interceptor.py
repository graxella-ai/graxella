"""graxella.healing.interceptor — drift interception (Phase 2, 2-4/2-5).

The B10 BrownBrillion port, made framework-agnostic and gate-governed.
``primary`` and ``fallback`` are plain callables ``dict -> Any`` — MCP
sessions, HTTP clients, LangChain tools all adapt in one lambda; the
interceptor never depends on any framework or model.

Everything a user touches lives in the graxella package: this module is
the canonical home; ``axon_fabric.interceptor`` re-exports it for
backward compatibility only.

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

import logging
import re
import uuid
from typing import Any, Callable, Optional

_log = logging.getLogger("graxella")

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

#: A 404 is drift only when the text says the ENDPOINT moved — a plain
#: "order 123 not found" is a normal miss, not an API migration.
_HTTP_404_CONTEXT = re.compile(
    r"endpoint|route|/api/|url|path|deprecat|no longer", re.IGNORECASE)

#: Signature mismatches beyond the classic "unexpected keyword".
_SIGNATURE_DRIFT = re.compile(
    r"missing \d+ required|required (?:keyword-only |positional )?"
    r"argument|field required|is a required property", re.IGNORECASE)

#: Exception TYPE names that indicate the tool's contract changed.
#: Duck-typed by name so pydantic/jsonschema/marshmallow are detected
#: without importing any of them.
_VALIDATION_TYPES = frozenset({
    "ValidationError", "SchemaError", "MissingRequiredArgument",
})


def classify_drift(exc: BaseException) -> str | None:
    """Classify an exception as schema/API drift, or None for a normal
    failure. The widened successor to the single-regex ``is_drift``:

      * ``signature``  — the classic explicit signals (regex, verbatim)
                         plus missing/unexpected-argument shapes
      * ``validation`` — a validation library rejected the args
                         (pydantic / jsonschema / marshmallow, by type
                         name — the payload no longer fits the schema)
      * ``http_gone``  — HTTP 410, or a 404 whose text says the ENDPOINT
                         moved (a plain record-miss 404 is NOT drift)

    Deliberately conservative: auth failures, timeouts, KeyErrors and
    plain 404s stay ordinary failures — loud, recorded, never healed.
    Pass a custom ``drift_classifier=`` to a ToolInterceptor / Session to
    widen or narrow this per deployment.
    """
    msg = str(exc)
    if DRIFT_SIGNATURE.search(msg) or _SIGNATURE_DRIFT.search(msg):
        return "signature"
    if type(exc).__name__ in _VALIDATION_TYPES:
        return "validation"
    # status codes: urllib's .code, requests' .response.status_code, or a
    # .status_code attribute on custom clients
    status = getattr(exc, "code", None) \
        or getattr(exc, "status_code", None) \
        or getattr(getattr(exc, "response", None), "status_code", None)
    if status == 410 or re.search(r"\b410\b|HTTP_410|\bGone\b", msg):
        return "http_gone"
    if (status == 404 or re.search(r"\b404\b", msg)) \
            and _HTTP_404_CONTEXT.search(msg):
        return "http_gone"
    return None

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
                 session_id: str | None = None,
                 skill_resolver: Callable[[str],
                                          Optional[Callable[[dict], Any]]]
                 | None = None,
                 drift_classifier: Callable[[BaseException], Optional[str]]
                 | None = None) -> None:
        self.primary = primary
        self.tool_name = tool_name
        self.memory = memory
        self.rulebook = rulebook
        self.gate = gate or EvidenceGate(memory)
        self.fallback = fallback
        self.healer = healer
        self.skill_resolver = skill_resolver
        self.drift_classifier = drift_classifier or classify_drift
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
            if self.drift_classifier(exc) is None:
                self._outcome(aid, ok=False, kind="tool", err=str(exc))
                raise
            return self._heal(aid, args, str(exc))
        self._outcome(aid, ok=True, kind="tool")
        return result

    # -- the heal ladder ------------------------------------------------------

    def _heal(self, aid: str, args: dict, error: str) -> Any:
        if self.fallback is None:
            self._outcome(aid, ok=False, kind="tool", err=error,
                          err_class="drift")
            raise RuntimeError(
                f"{self.tool_name} drifted and no fallback is configured: "
                f"{error}")

        # Rung 2: a promoted, gate-approved transform. Deterministic.
        rule = self.rulebook.find_substitution(self.tool_name)
        if rule is not None and rule.recipe:
            recipe = TransformRecipe.from_dict(rule.recipe)
            # Honor the rule's named substitute. Heal-promoted transform
            # rules leave with_skill empty (the configured fallback IS the
            # designated new endpoint); a tool_binding rule that NAMES a
            # different target must dispatch to that target — applying its
            # recipe to an unrelated fallback executes the rule against
            # something it never cited. Unresolvable targets degrade to
            # the fallback LOUDLY, never silently.
            target = self.fallback
            sub = (rule.with_skill or "").strip()
            if sub and sub != self.tool_name:
                resolved = self.skill_resolver(sub) if self.skill_resolver \
                    else None
                if resolved is not None:
                    target = resolved
                else:
                    _log.warning(
                        "graxella: promoted rule %s substitutes %r with %r "
                        "but no resolver/target is available here — using "
                        "the configured fallback instead. The rule's cited "
                        "evidence is about %r; review this dispatch.",
                        rule.id, self.tool_name, sub, sub)
            try:
                result = target(recipe.apply(dict(args)))
            except Exception as exc:
                # A gate-approved rule that FAILS is evidence against its
                # tuple (the gate's posterior sees it) AND against the rule
                # itself, specifically — a rule-scoped touch (exact id, no
                # cross-tuple bleed) is the raw material Session.reconcile()
                # reads to demote it. Record, then fail loudly.
                self._outcome(aid, ok=False, kind="transform",
                              err=f"promoted rule {rule.id} failed: {exc}",
                              err_class="drift")
                self._touch_rule_health(aid, rule.id, ok=False)
                raise RuntimeError(
                    f"{self.tool_name}: promoted heal rule {rule.id} "
                    f"failed against the live target: {exc}") from exc
            self._outcome(aid, ok=True, kind="transform")
            self._touch_rule_health(aid, rule.id, ok=True)
            return result

        # Rung 2.5: a recipe was already proposed this process and is
        # awaiting review — reuse it deterministically, zero LLM. A recipe
        # that FAILS here is discarded (never reused again) and the
        # failure is recorded — a broken cache must not loop.
        if self._proposed_recipe is not None:
            try:
                result = self.fallback(self._proposed_recipe.apply(dict(args)))
            except Exception as exc:
                self._proposed_recipe = None
                self._outcome(aid, ok=False, kind="transform",
                              err=f"cached heal recipe failed: {exc}",
                              err_class="drift")
                raise RuntimeError(
                    f"{self.tool_name}: cached heal recipe failed and was "
                    f"discarded (the healer may re-propose on the next "
                    f"call): {exc}") from exc
            self._outcome(aid, ok=True, kind="transform")
            return result

        # Rung 3: heal once, then propose — never heal silently twice.
        if self.healer is not None:
            self.healer_calls += 1
            recipe = self.healer(self.tool_name, dict(args), error)
            if recipe is not None:
                self._proposed_recipe = recipe
                healed_args = recipe.apply(dict(args))
                try:
                    result = self.fallback(healed_args)
                except Exception as exc:
                    # Rung 4 via a bad proposal: the healed call itself
                    # failed. Discard the recipe (never cache a broken
                    # one), record the typed failure, raise loud.
                    self._proposed_recipe = None
                    self._outcome(aid, ok=False, kind="transform",
                                  err=f"healed call failed: {exc}",
                                  err_class="drift")
                    raise RuntimeError(
                        f"{self.tool_name} drifted; the healer's proposed "
                        f"recipe did not fix it (recipe discarded): "
                        f"{exc}") from exc
                self._outcome(aid, ok=True, kind="transform")
                proposal = recipe.to_proposal(
                    domain=self.domain, tool=self.tool_name,
                    origin="healer:axon-fabric", model_id=self.model_id)
                # self_certified: `expected` IS this recipe's own output,
                # so the win is tautological — recorded for the reviewer,
                # excluded from the gate's posterior fusion.
                report = audit(proposal, [ReplayCase(
                    case_id=f"live::{aid}", inputs=dict(args),
                    expected=healed_args, source_ids=(aid,))],
                    memory=self.memory, self_certified=True)
                proposal = with_replay_evidence(proposal, report)
                # Forward-provenance edge: this heal decision created this
                # proposal. Makes "what did this repair produce?" and (via
                # touching(proposal)) "which incident spawned this rule?"
                # queryable. Never let a provenance write break the heal.
                rt = getattr(self.memory, "record_touch", None)
                if rt is not None:
                    try:
                        rt(aid, proposal.id, role="proposal",
                           detail={"tool": self.tool_name})
                    except Exception:
                        pass
                self.gate.refresh()
                self.gate.decide(proposal)   # cold -> human review queue
                return result

        self._outcome(aid, ok=False, kind="tool", err=error,
                      err_class="drift")
        raise RuntimeError(
            f"{self.tool_name} drifted; no promoted transform and no "
            f"healer produced one: {error}")

    def _touch_rule_health(self, aid: str, rule_id: str, *, ok: bool) -> None:
        """Rule-scoped touch (exact rule id — the P0-1 lesson applied
        here too): a dedicated, per-rule tally that ``Rulebook.demote``'s
        caller reads, independent of the tool-scoped aggregate the gate
        uses for NEW proposals. Never let a provenance write break the
        heal it's recording."""
        rt = getattr(self.memory, "record_touch", None)
        if rt is None:
            return
        try:
            rt(aid, rule_id, role="rule_use", detail={"ok": ok})
        except Exception:
            pass

    def standing_proposal(self):
        """Rebuild the transform Proposal this interceptor healed to, if any
        (rung 3 fired and a recipe is awaiting promotion). Returns a
        ``spec.Proposal`` with the SAME deterministic id as the one first
        decided — so re-deciding it against fresh ledger evidence continues
        the same proposal's history. ``None`` when nothing has been healed.

        Used by the autonomous promotion pass (``Session.reconcile``): the
        recipe lives here, so this is where a re-decide reconstructs it."""
        if self._proposed_recipe is None:
            return None
        return self._proposed_recipe.to_proposal(
            domain=self.domain, tool=self.tool_name,
            origin="healer:axon-fabric", model_id=self.model_id)

    def _outcome(self, aid: str, *, ok: bool, kind: str,
                 err: str | None = None,
                 err_class: str | None = None) -> None:
        self.memory.record_outcome(
            decision_id=aid, ok=ok, kind=kind, err=err,
            err_class=err_class or (
                "drift" if (err and is_drift(err)) else None),
            domain=self.domain, chosen=self.tool_name,
            model_id=self.model_id, session_id=self.session_id)


def is_drift(message: str) -> bool:
    return bool(DRIFT_SIGNATURE.search(message))


__all__ = ["ToolInterceptor", "Healer", "is_drift", "DRIFT_SIGNATURE"]
