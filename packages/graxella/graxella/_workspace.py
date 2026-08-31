"""Workspace bootstrap for sibling packages (source-checkout fallback).

The supported path is the uv workspace: ``uv sync`` at the repo root
installs graxella and agent2society editable into one venv, and this
module then does nothing. The shim below exists only so a bare source
checkout (no install at all) can still ``import graxella``.

Only agent2society needs this now: the memory engine lives inside the
package as ``graxella.mnema`` and imports like any other submodule.

Layouts probed, in order:
  * monorepo (S-3+):  <repo>/packages/agent2society/src
  * legacy (pre-S-3): <repo>/agent2society/src
"""
from __future__ import annotations

import sys
from pathlib import Path

_PKG_PARENT = Path(__file__).resolve().parent.parent  # packages/graxella (or legacy repo root)

_CANDIDATE_ROOTS = (
    _PKG_PARENT.parent,   # packages/  -> packages/mnema, packages/agent2society
    _PKG_PARENT,          # legacy     -> <repo>/mnema, <repo>/agent2society
)


def _ensure_on_path() -> None:
    for pkg in ("agent2society",):
        if pkg in sys.modules and getattr(sys.modules[pkg], "__file__", None):
            continue  # properly installed — the shim has no business here
        for root in _CANDIDATE_ROOTS:
            src_dir = root / pkg / "src"
            if not src_dir.is_dir():
                continue
            as_str = str(src_dir)
            # Ensure our src path wins over any namespace-package resolution
            # that Python's default finders may have already assembled.
            if as_str in sys.path:
                sys.path.remove(as_str)
            sys.path.insert(0, as_str)
            # Evict any stale namespace-package binding so the next real
            # import resolves through the src/ package.
            cached = sys.modules.get(pkg)
            if cached is not None and getattr(cached, "__file__", None) is None:
                sys.modules.pop(pkg, None)
            break


_ensure_on_path()
