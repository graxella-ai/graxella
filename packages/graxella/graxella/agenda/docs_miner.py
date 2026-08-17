"""DocsMiner — turn a KnowledgeSeed into rulebook Proposals.

The other three miners (RuleDistiller, CapabilityReweigher, TrustPromoter)
need lived Experience. DocsMiner needs none — the moment a docs export lands
it can propose rules a reviewer can promote. That's the whole point:
intelligence before traffic.

Emits two proposal shapes, both compatible with the existing Rulebook:

  * kind='rule'
    subject='<intent?>:<old>-><new>'
    change={if_intent: '<intent?>', replace_skill: old, with_skill: new,
            recipe: {field_map: {...}}}          # field_map from field_maps_to

  * kind='rule'  (no field_map — pure substitution, args passed through)
    when the seed has no field-level renames documented for this pair.

Confidence policy:
  * Docs alone: cap at 0.5 — a documented deprecation is a hint, not proof.
  * Docs + lived experience: bump if RuleDistiller ALSO emitted the same
    substitution. We do that boost inside the runner, not here — the miner
    stays a pure function of its inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from graxella.agenda.miners import Miner, Proposal, _proposal_id
from graxella.knowledge.seed import KnowledgeSeed
from graxella.memory.episode import ExperienceEpisode


@dataclass
class DocsMiner(Miner):
    """Miner that reads a KnowledgeSeed. Episodes are ignored on purpose —
    the runner still passes them for uniformity, but this miner is
    documentation-only. Combine with RuleDistiller for compounded confidence.
    """
    seed: KnowledgeSeed
    default_intent: str = ""
    max_confidence: float = 0.5   # docs-only cap; overridden by cross-corroboration
    name: str = field(default="docs_miner", init=False)

    def mine(self, episodes: list[ExperienceEpisode]) -> list[Proposal]:  # noqa: ARG002
        proposals: list[Proposal] = []
        for old, new, assertion in self.seed.deprecations():
            field_map = self.seed.field_map_for(old, new)
            intent = self.default_intent
            # If the docs assert an intent for either tool, prefer that.
            for a in self.seed.about(new):
                if a.predicate == "has_intent":
                    intent = a.object
                    break
            subject = f"{intent}:{old}->{new}" if intent else f"{old}->{new}"
            change: dict[str, Any] = {
                "if_intent": intent,
                "replace_skill": old,
                "with_skill": new,
            }
            if field_map:
                change["recipe"] = {"field_map": field_map}

            evidence_lines = [
                f"docs: {assertion.evidence}",
                f"source: {assertion.source_uri}",
            ]
            if field_map:
                evidence_lines.append(
                    f"field_map derived from documented renames: {field_map}"
                )

            proposals.append(Proposal(
                id=_proposal_id("rule", subject),
                kind="rule",
                subject=subject,
                change=change,
                evidence=" | ".join(evidence_lines),
                derived_from=[assertion.source_uri],
                confidence=min(self.max_confidence, assertion.confidence),
            ))
        return proposals
