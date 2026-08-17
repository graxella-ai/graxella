"""graxella.rulebook — approved graph mutations, on disk, hot-reloadable.

The output of the human-in-the-loop step. Proposals a reviewer promoted
become entries here; the runtime consults the Rulebook at dispatch time
so a newly-promoted rule takes effect on the next call — no restart, no
redeploy.

Public surface:
    Rulebook       — the on-disk store
    ApprovedRule   — one promoted proposal
"""
from graxella.rulebook.store import ApprovedRule, Rulebook

__all__ = ["ApprovedRule", "Rulebook"]
