"""File loaders — walk a path, return (source_uri, text) pairs.

Supported by default: `.md`, `.markdown`, `.txt`. Everything else is skipped
with a warning. PDF and HTML land behind opt-in extras.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown", ".txt"})


@dataclass
class LoadedDoc:
    source: str    # relative path (or absolute, if outside project) — becomes URI stem
    text: str


def _iter_paths(paths: Iterable[str | Path]) -> Iterator[Path]:
    for p in paths:
        pth = Path(p)
        if pth.is_file():
            yield pth
        elif pth.is_dir():
            for f in sorted(pth.rglob("*")):
                if f.is_file():
                    yield f


def load(paths: Iterable[str | Path]) -> list[LoadedDoc]:
    """Read every supported file under `paths`. Directories are walked
    recursively; unsupported extensions are silently skipped (they'd otherwise
    trigger a stream of noise on a real docs tree).
    """
    docs: list[LoadedDoc] = []
    for p in _iter_paths(paths):
        if p.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = p.read_text(encoding="utf-8", errors="replace")
        docs.append(LoadedDoc(source=str(p).replace("\\", "/"), text=text))
    return docs
