from __future__ import annotations

from mnema.core.consolidation import Digest


def diff_digests(d1: Digest, d2: Digest) -> dict:
    """Return a structured diff between two Digest versions."""
    v1_rule_ids = {r.id for r in d1.rules}
    v2_rule_ids = {r.id for r in d2.rules}
    v1_skill_ids = {s.id for s in d1.skills}
    v2_skill_ids = {s.id for s in d2.skills}
    return {
        "from_version": d1.version,
        "to_version": d2.version,
        "rules": {
            "added": [r.model_dump(mode="json") for r in d2.rules if r.id not in v1_rule_ids],
            "removed": [r.model_dump(mode="json") for r in d1.rules if r.id not in v2_rule_ids],
            "retained_ids": [r.id for r in d2.rules if r.id in v1_rule_ids],
            "scope_updated": [
                {"scope": r2.scope, "old_id": r1.id, "new_id": r2.id}
                for r2 in d2.rules
                for r1 in d1.rules
                if r2.scope == r1.scope and r2.id != r1.id
            ],
        },
        "skills": {
            "added": [s.model_dump(mode="json") for s in d2.skills if s.id not in v1_skill_ids],
            "removed": [s.model_dump(mode="json") for s in d1.skills if s.id not in v2_skill_ids],
            "retained_ids": [s.id for s in d2.skills if s.id in v1_skill_ids],
            "name_updated": [
                {"name": s2.name, "old_id": s1.id, "new_id": s2.id}
                for s2 in d2.skills
                for s1 in d1.skills
                if s2.name == s1.name and s2.id != s1.id
            ],
        },
        "render_diff": {
            "from_chars": len(d1.render()),
            "to_chars": len(d2.render()),
        },
    }
