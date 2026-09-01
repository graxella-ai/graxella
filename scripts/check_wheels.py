"""Wheel-install truth check (build plan task 0C-1).

Builds the three workspace wheels, installs them into a scratch venv
(pulling third-party deps from PyPI), and smokes the flagship import
surface from a NEUTRAL working directory — the exact scenario the old
packaging silently failed.

Run from the repo root:  uv run python scripts/check_wheels.py
Exit code 0 = the published-artifact story is true.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGES = ["packages/agent2society", "packages/graxella"]


def _uv_cmd() -> list[str]:
    """Locate `uv`, robust to how it got installed.

    Two real environments disagree here: astral-sh/setup-uv (CI) puts a
    standalone `uv` binary on PATH but never `pip install`s it into the
    venv this script runs in, so `python -m uv` fails with "No module
    named uv". A dev machine that did `pip install uv` into its own venv
    has the opposite gap: `uv` sits next to that venv's `python.exe`, but
    unless the venv is shell-activated, that directory is never on PATH,
    so a bare `uv` subprocess call fails with WinError 2 / "not found".
    Try PATH first, then the interpreter's own bin/Scripts directory.
    """
    found = shutil.which("uv")
    if found:
        return [found]
    sibling = Path(sys.executable).parent / (
        "uv.exe" if sys.platform == "win32" else "uv")
    if sibling.exists():
        return [str(sibling)]
    return [sys.executable, "-m", "uv"]

SMOKE = """
import graxella
from graxella import mesh, Memory, Society
from graxella.gate import spec
from graxella.beliefs.records import OutcomeRecord
p = spec.Proposal(kind=spec.ArtifactKind.TRANSFORM,
                  target=spec.TargetScope(domain='smoke'),
                  payload={}, origin='miner:smoke')
print('wheel smoke OK:', graxella.__version__, p.id[:14])
"""


def run(cmd: list[str], **kw) -> None:
    print("$", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def main() -> int:
    uv = _uv_cmd()
    dist = REPO / "dist"
    for pkg in PACKAGES:
        run([*uv, "build", str(REPO / pkg), "--out-dir", str(dist)])

    wheels = sorted(dist.glob("*.whl"))
    print(f"built {len(wheels)} wheels: {[w.name for w in wheels]}")

    with tempfile.TemporaryDirectory(prefix="graxella-wheelcheck-") as td:
        venv = Path(td) / "venv"
        run([*uv, "venv", str(venv)])
        py = (venv / "Scripts" / "python.exe" if sys.platform == "win32"
              else venv / "bin" / "python")
        run([*uv, "pip", "install", "--python", str(py), *map(str, wheels)])
        # Neutral cwd: the temp dir, far from any folder named "graxella".
        run([str(py), "-c", SMOKE], cwd=td)
    print("PASS: wheels install and import standalone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
