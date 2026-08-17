"""graxella.knowledge — turn documentation into a KnowledgeSeed.

Read-path counterpart to the write-path Experience store: whereas
`graxella.memory` captures what agents DID, `graxella.knowledge` captures
what humans WROTE about the system. Both feed the same rulebook, both
cite the same audit surface.

Public surface:
  * from_docs(paths, *, backend='lite')   -> KnowledgeSeed
  * Assertion, KnowledgeSeed              -> the data types

Backends:
  * 'lite'   (default) — regex extractors, zero heavy deps. Deterministic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Literal

from graxella.knowledge.extractors import extract_all
from graxella.knowledge.loaders import LoadedDoc, load
from graxella.knowledge.seed import Assertion, KnowledgeSeed

__all__ = ["from_docs", "Assertion", "KnowledgeSeed", "LoadedDoc"]


def from_docs(paths: str | Path | Iterable[str | Path],
              *,
              backend: Literal["lite"] = "lite") -> KnowledgeSeed:
    """Load every supported doc under `paths`, run the extractors, return a
    KnowledgeSeed. Accepts a single path or an iterable.

    `paths` may include files and directories mixed together; directories are
    walked recursively.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    if backend != "lite":  # pragma: no cover
        raise ValueError(f"unknown backend {backend!r}. Only 'lite' is wired today.")

    docs = load(paths)
    assertions: list[Assertion] = []
    for d in docs:
        assertions.extend(extract_all(d.text, d.source))
    return KnowledgeSeed(assertions=assertions)
