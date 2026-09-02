"""CI gate: packages/graxella/README.md must exactly match the repo-root
README.md (module docstring in sync_readme.py explains why a copy exists
at all). Fails loudly, with the fix command, rather than letting PyPI's
package description silently drift from what GitHub shows -- which is
exactly how v0.1.0 shipped the wrong text in the first place.

Run:  uv run python scripts/check_readme_sync.py
"""
from __future__ import annotations

import sys

# Python auto-prepends this script's own directory to sys.path on direct
# execution, so `sync_readme` (the sibling module) is already importable.
from sync_readme import DST, MARKER, SRC


def main() -> int:
    expected = MARKER + SRC.read_text(encoding="utf-8")
    actual = DST.read_text(encoding="utf-8") if DST.exists() else ""
    if actual == expected:
        print(f"OK: {DST.relative_to(DST.parents[2])} matches the root README")
        return 0
    print(f"FAIL: {DST} is out of sync with {SRC}", file=sys.stderr)
    print("  fix: uv run python scripts/sync_readme.py", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
