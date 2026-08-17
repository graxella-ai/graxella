"""Offline runner — the only thing the agenda actually invokes.

Reads a snapshot of the ExperienceStore, runs the configured miners, and
writes a proposals.json. It never touches the live runtime. Promotion of a
proposal into production is a separate, human-gated step.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graxella.agenda.miners import DEFAULT_MINERS, Miner, Proposal
from graxella.memory.episode import ExperienceStore


@dataclass
class HiddenAgendaRunner:
    """Bundles a store + miner set. Isolated from the live runtime by design."""
    store: ExperienceStore
    miners: list[Miner] = field(default_factory=lambda: list(DEFAULT_MINERS))

    def run(self) -> list[Proposal]:
        episodes = self.store.all()
        proposals: list[Proposal] = []
        for miner in self.miners:
            proposals.extend(miner.mine(episodes))
        return proposals

    def dump(self, path: str | Path) -> dict[str, Any]:
        """Run the miners, serialise to disk, and return the payload."""
        proposals = self.run()
        payload = {
            "episodes": len(self.store.all()),
            "miners": [m.name for m in self.miners],
            "proposals": [p.to_dict() for p in proposals],
        }
        Path(path).write_text(json.dumps(payload, indent=2, default=str))
        return payload
