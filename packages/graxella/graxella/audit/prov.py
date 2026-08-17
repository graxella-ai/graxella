"""PROV-O audit emitter — JSON-LD, no rdflib dependency.

Every promoted rule ships with a provenance graph so an auditor (EU AI Act,
SOC2, internal review) can answer three questions without asking a human:

  1. What evidence supports this rule?  (Episode ids → tool_calls)
  2. Who or what derived it?             (Miner name + Proposal id)
  3. Who approved it, and when?          (approved_by + approved_at)

We emit W3C PROV-O terms using a JSON-LD @context. This keeps the file
human-readable while remaining machine-consumable by any PROV toolchain
(Neo4j n10s, Apache Jena, ProvStore, etc.). Zero heavy deps — the format
is stable, small, and diff-friendly for code review.

PROV-O mapping used here:
  * Episode        -> prov:Entity        (the lived experience)
  * Miner          -> prov:SoftwareAgent (the offline learner)
  * Proposal       -> prov:Entity        (mined suggestion; wasDerivedFrom episodes,
                                          wasAttributedTo miner)
  * Reviewer       -> prov:Person        (human approver)
  * Promotion      -> prov:Activity      (approval event; used proposal,
                                          generated approved rule, wasAssociatedWith reviewer)
  * ApprovedRule   -> prov:Entity        (wasGeneratedBy promotion, wasDerivedFrom proposal)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from graxella.agenda.miners import Proposal
from graxella.memory.episode import ExperienceEpisode, ExperienceStore
from graxella.rulebook import ApprovedRule, Rulebook

_CONTEXT: dict[str, Any] = {
    "prov":     "http://www.w3.org/ns/prov#",
    "xsd":      "http://www.w3.org/2001/XMLSchema#",
    "grx":      "https://graxella.ai/ns#",
    "id":       "@id",
    "type":     "@type",
    "Entity":         "prov:Entity",
    "Activity":       "prov:Activity",
    "Agent":          "prov:Agent",
    "SoftwareAgent":  "prov:SoftwareAgent",
    "Person":         "prov:Person",
    "wasDerivedFrom":     {"@id": "prov:wasDerivedFrom", "@type": "@id", "@container": "@set"},
    "wasAttributedTo":    {"@id": "prov:wasAttributedTo", "@type": "@id"},
    "wasGeneratedBy":     {"@id": "prov:wasGeneratedBy", "@type": "@id"},
    "wasAssociatedWith":  {"@id": "prov:wasAssociatedWith", "@type": "@id"},
    "used":               {"@id": "prov:used", "@type": "@id", "@container": "@set"},
    "startedAtTime":      {"@id": "prov:startedAtTime", "@type": "xsd:dateTime"},
    "endedAtTime":        {"@id": "prov:endedAtTime", "@type": "xsd:dateTime"},
    "generatedAtTime":    {"@id": "prov:generatedAtTime", "@type": "xsd:dateTime"},
}


def _iso(ts: float) -> str:
    """Unix seconds → ISO-8601 UTC. PROV requires xsd:dateTime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _episode_iri(episode_id: str) -> str:
    return f"grx:episode/{episode_id}"


def _proposal_iri(proposal_id: str) -> str:
    return f"grx:proposal/{proposal_id}"


def _rule_iri(rule_id: str) -> str:
    return f"grx:rule/{rule_id}"


def _promotion_iri(rule_id: str) -> str:
    return f"grx:promotion/{rule_id}"


def _miner_iri(miner_name: str) -> str:
    return f"grx:miner/{miner_name}"


def _reviewer_iri(approved_by: str) -> str:
    # Reviewers are addressed by handle. Free-form is fine — auditors resolve
    # the mapping to a real identity out-of-band (SSO log, ticket system).
    return f"grx:reviewer/{approved_by}"


@dataclass
class AuditBundle:
    """One export payload — proposals + rules + evidence, all in one graph."""

    proposals: list[Proposal]
    approved_rules: list[ApprovedRule]
    episodes: list[ExperienceEpisode]
    miner_name: str = "hidden_agenda"

    def to_jsonld(self) -> dict[str, Any]:
        graph: list[dict[str, Any]] = []
        episodes_by_id = {e.id: e for e in self.episodes}

        # -- Episodes (evidence) -----------------------------------------
        for e in self.episodes:
            node = {
                "id": _episode_iri(e.id),
                "type": "Entity",
                "grx:kind": "episode",
                "grx:session": e.session_id,
                "grx:intent": e.intent,
                "grx:agent": e.agent_uri,
                "grx:ok": e.ok,
                "grx:toolCalls": [
                    {
                        "grx:toolId": tc.tool_id,
                        "grx:ok":     tc.ok,
                        "grx:err":    tc.err or "",
                        "grx:latencyMs": tc.latency_ms,
                    }
                    for tc in e.tool_calls
                ],
            }
            if e.started_at:
                node["startedAtTime"] = _iso(e.started_at)
            if e.ended_at:
                node["endedAtTime"] = _iso(e.ended_at)
            graph.append(node)

        # -- Miner (software agent) --------------------------------------
        graph.append({
            "id": _miner_iri(self.miner_name),
            "type": "SoftwareAgent",
            "grx:kind": "miner",
            "grx:name": self.miner_name,
        })

        # -- Proposals ---------------------------------------------------
        for p in self.proposals:
            evidence_iris = [
                _episode_iri(eid) for eid in p.derived_from
                if eid in episodes_by_id
            ]
            graph.append({
                "id": _proposal_iri(p.id),
                "type": "Entity",
                "grx:kind": "proposal",
                "grx:proposalKind": p.kind,
                "grx:subject":      p.subject,
                "grx:evidence":     p.evidence,
                "grx:confidence":   p.confidence,
                "grx:change":       p.change,
                "wasDerivedFrom":   evidence_iris,
                "wasAttributedTo":  _miner_iri(self.miner_name),
            })

        # -- Reviewers + Promotions + ApprovedRules ----------------------
        seen_reviewers: set[str] = set()
        for r in self.approved_rules:
            if r.approved_by and r.approved_by not in seen_reviewers:
                seen_reviewers.add(r.approved_by)
                graph.append({
                    "id": _reviewer_iri(r.approved_by),
                    "type": "Person",
                    "grx:kind":   "reviewer",
                    "grx:handle": r.approved_by,
                })

            promotion_node = {
                "id": _promotion_iri(r.id),
                "type": "Activity",
                "grx:kind": "promotion",
                "used":              [_proposal_iri(r.proposal_id)],
                "wasAssociatedWith": _reviewer_iri(r.approved_by or "human"),
            }
            if r.approved_at:
                promotion_node["startedAtTime"] = _iso(r.approved_at)
                promotion_node["endedAtTime"]   = _iso(r.approved_at)
            graph.append(promotion_node)

            evidence_iris = [
                _episode_iri(eid) for eid in r.derived_from
                if eid in episodes_by_id
            ]
            rule_node: dict[str, Any] = {
                "id": _rule_iri(r.id),
                "type": "Entity",
                "grx:kind":         "approved_rule",
                "grx:ruleKind":     r.kind,
                "grx:intent":       r.intent,
                "grx:replaceSkill": r.replace_skill,
                "grx:withSkill":    r.with_skill,
                "grx:recipe":       r.recipe,
                "grx:change":       r.change,
                "wasGeneratedBy":   _promotion_iri(r.id),
                "wasDerivedFrom":   [_proposal_iri(r.proposal_id)] + evidence_iris,
            }
            if r.approved_at:
                rule_node["generatedAtTime"] = _iso(r.approved_at)
            graph.append(rule_node)

        return {"@context": _CONTEXT, "@graph": graph}


def export(store: ExperienceStore,
           rulebook: Rulebook,
           proposals: Iterable[Proposal],
           *,
           path: str | Path,
           miner_name: str = "hidden_agenda") -> dict[str, Any]:
    """Write a PROV-O JSON-LD bundle to `path`. Returns the payload written.

    Includes ALL Episodes cited by ANY proposal or rule — pruning the store
    to only referenced Episodes keeps audit files small and reproducible.
    """
    prop_list = list(proposals)
    rules = rulebook.all_rules()
    cited: set[str] = set()
    for p in prop_list:
        cited.update(p.derived_from)
    for r in rules:
        cited.update(r.derived_from)
    all_eps = store.all()
    eps = [e for e in all_eps if e.id in cited]

    bundle = AuditBundle(
        proposals=prop_list,
        approved_rules=rules,
        episodes=eps,
        miner_name=miner_name,
    )
    payload = bundle.to_jsonld()
    Path(path).write_text(json.dumps(payload, indent=2, default=str))
    return payload
