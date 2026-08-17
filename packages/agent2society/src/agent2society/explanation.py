"""Human-readable routing explanations.

Telemetry tells you what happened. An Explanation tells you *why* -- in
sentences a human can read, with the numeric features that fired, the
alternatives that were considered, and the chosen agent's self-declared
caveats. This is the surface that turns ledger entries into auditable
decisions.

An Explanation is built by `Mesh._explain_route()` after routing and
returned (via `mesh.explain(handoff_id)`) once dispatch is complete.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .embeddings import tokenize
from .router import RouteCandidate


@dataclass
class RoutingExplanation:
    """Human-readable account of a single routing decision.

    Always produced -- there is no path through `mesh.run()` that does
    NOT generate one. If routing failed (no candidate cleared min_score,
    or all failed conformance), the explanation still exists; its
    `chosen_*` fields are None and `rationale` says why.
    """

    handoff_id: str
    task: str
    intent: str
    chosen_agent: Optional[str]
    chosen_skill: Optional[str]
    rationale: str
    features_fired: Dict[str, float]
    alternatives: List[RouteCandidate]
    confidence: float
    agent_self_caveats: List[str] = field(default_factory=list)
    blocked_reason: Optional[str] = None
    prior_chain: List[Dict[str, Any]] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    # v0.5.2 routing-quality signals (zero-cost: derived from the
    # already-sorted candidates list before any I/O).
    margin: float = 0.0          # score gap between top-1 and top-2 candidate
    flags: Tuple[str, ...] = ()  # "OOD" | "VECTOR_AMBIGUITY" | "LOW_MARGIN"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "task": self.task,
            "intent": self.intent,
            "chosen_agent": self.chosen_agent,
            "chosen_skill": self.chosen_skill,
            "rationale": self.rationale,
            "features_fired": dict(self.features_fired),
            "alternatives": [c.to_dict() for c in self.alternatives],
            "confidence": round(self.confidence, 4),
            "margin": round(self.margin, 4),
            "flags": list(self.flags),
            "agent_self_caveats": list(self.agent_self_caveats),
            "blocked_reason": self.blocked_reason,
            "prior_chain": list(self.prior_chain),
            "assumptions": list(self.assumptions),
        }

    def render(self) -> str:
        """ASCII-safe one-screen view. Safe on Windows cp1252 consoles."""
        lines: List[str] = []
        lines.append(f"handoff {self.handoff_id}  task: {_clip(self.task, 70)}")
        if self.intent:
            lines.append(f"  intent: {_clip(self.intent, 76)}")
        if self.prior_chain:
            lines.append(f"  chain  : {len(self.prior_chain)} prior step(s)")
            for i, step in enumerate(self.prior_chain, 1):
                lines.append(
                    f"    {i}. {step.get('agent')} :: {step.get('skill')} "
                    f"-> {_clip(step.get('summary', ''), 50)}"
                )
        if self.assumptions:
            lines.append("  assumes:")
            for a in self.assumptions:
                lines.append(f"    - {a}")
        if self.chosen_agent:
            lines.append(
                f"  chose  : {self.chosen_agent} :: {self.chosen_skill} "
                f"(confidence={self.confidence:.3f}  margin={self.margin:.3f})"
            )
        else:
            lines.append("  chose  : NONE")
            if self.blocked_reason:
                lines.append(f"  blocked: {self.blocked_reason}")
        if self.flags:
            lines.append(f"  flags  : {' '.join(self.flags)}")
        lines.append(f"  why    : {self.rationale}")
        if self.features_fired:
            feats = ", ".join(
                f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                for k, v in self.features_fired.items()
            )
            lines.append(f"  features: {feats}")
        if self.alternatives:
            lines.append("  alternatives:")
            for c in self.alternatives:
                tag = "  REJECTED" if c.rejected_reason else ""
                lines.append(
                    f"    - {c.agent} :: {c.skill_id}  "
                    f"score={c.score:.3f} sem={c.semantic:.3f} "
                    f"tags={c.tag_overlap:.3f}{tag}"
                )
                if c.rejected_reason:
                    lines.append(f"        reason: {c.rejected_reason}")
        if self.agent_self_caveats:
            lines.append("  agent self-caveats:")
            for c in self.agent_self_caveats:
                lines.append(f"    - {c}")
        return "\n".join(lines)


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def build_rationale(
    *,
    task: str,
    chosen: Optional[RouteCandidate],
    alternatives: Sequence[RouteCandidate],
    blocked_reason: Optional[str],
) -> str:
    """Deterministic one-paragraph rationale. No LLM in this path.

    The text is template-driven: it names the chosen pair, the features
    that fired, and the strongest reason the runner-up was not chosen.
    Same inputs -> same rationale, every time.
    """
    if chosen is None:
        if blocked_reason:
            return f"no agent dispatched: {blocked_reason}"
        return "no candidate scored above the minimum threshold"

    matched = ", ".join(chosen.matched_tokens[:4]) or "(none above threshold)"
    tags = ", ".join(chosen.matched_tags[:4])
    parts = [
        f"{chosen.agent}::{chosen.skill_id} selected (score={chosen.score:.3f}).",
        f"Semantic match {chosen.semantic:.3f} on tokens [{matched}]"
        + (f"; tag overlap {chosen.tag_overlap:.3f} on [{tags}]." if tags else "."),
    ]
    # Why not the next-best?
    others = [c for c in alternatives if c is not chosen]
    if others:
        runner = others[0]
        gap = chosen.score - runner.score
        if runner.rejected_reason:
            parts.append(
                f"Runner-up {runner.agent}::{runner.skill_id} rejected: "
                f"{runner.rejected_reason}."
            )
        else:
            parts.append(
                f"Runner-up {runner.agent}::{runner.skill_id} scored "
                f"{runner.score:.3f} (gap {gap:.3f})."
            )
    return " ".join(parts)


def explain_matched_tokens(
    task: str, skill_search_text: str, top_k: int = 6
) -> List[str]:
    """Surface which task tokens appear in the chosen skill's text.

    This is the audit hook: a reader can see exactly which words drove
    the semantic match, without needing to recompute embeddings.
    """
    task_tokens = set(tokenize(task))
    skill_tokens = set(tokenize(skill_search_text))
    common = sorted(task_tokens & skill_tokens)
    return common[:top_k]
