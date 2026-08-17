"""Pluggable persistence for routing explanations.

The default `InMemoryStore` keeps explanations in a dict for the lifetime
of the process -- enough for tests, notebooks, and short-running scripts.
Production deployments that want to audit decisions across restarts use
`JsonlFileStore`, which appends each explanation as a single JSON line
and rebuilds the index on construction.

`ExplanationStore` is a thin Mapping-like protocol so the optimizer and
governance machinery can read past decisions through the same interface
the Society uses internally. New stores (sqlite, redis, S3) can be added
later without touching the rest of the library.

Threading: stores are protected by an internal lock so concurrent
`Society.run()` calls do not interleave during writes. Readers use the
same lock for snapshot consistency.
"""
from __future__ import annotations

import json
import os
import threading
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

from ._logging import get_logger
from .explanation import RoutingExplanation
from .router import RouteCandidate

_log = get_logger(__name__)


@runtime_checkable
class ExplanationStore(Protocol):
    """Mapping-like protocol every store implements.

    Implementations must be safe to call from multiple threads. Order of
    insertion must be preserved by `ids()` and `all()` so reports render
    deterministically.
    """

    def put(self, exp: RoutingExplanation) -> None: ...
    def get(self, handoff_id: str) -> Optional[RoutingExplanation]: ...
    def all(self) -> Sequence[RoutingExplanation]: ...
    def ids(self) -> Sequence[str]: ...
    def __len__(self) -> int: ...
    def __contains__(self, handoff_id: object) -> bool: ...


class _MappingView(Mapping[str, RoutingExplanation]):
    """Frozen snapshot Mapping handed to code that wants a plain
    `Mapping[str, RoutingExplanation]` (notably, the optimizer).

    A `_MappingView` is built once from a point-in-time snapshot of the
    underlying store. Subsequent `put()` calls on the store are NOT
    reflected -- the snapshot is intentionally stable so multi-step
    consumers (the optimizer backtest, in particular) see consistent
    state for the entire duration of their work.
    """

    def __init__(self, by_id: Dict[str, RoutingExplanation], order: List[str]) -> None:
        # Defensive copies: callers must not be able to mutate the snapshot
        # by holding references to the source store's internals.
        self._by_id: Dict[str, RoutingExplanation] = dict(by_id)
        self._order: List[str] = list(order)

    def __getitem__(self, handoff_id: str) -> RoutingExplanation:
        if handoff_id not in self._by_id:
            raise KeyError(handoff_id)
        return self._by_id[handoff_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._order)

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, key: object) -> bool:
        return key in self._by_id


class InMemoryStore:
    """The default. Holds explanations in a dict for the process lifetime."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: Dict[str, RoutingExplanation] = {}
        # Separate insertion-order list so deletes (none yet) would not
        # break iteration order.
        self._order: List[str] = []

    def put(self, exp: RoutingExplanation) -> None:
        with self._lock:
            if exp.handoff_id not in self._by_id:
                self._order.append(exp.handoff_id)
            self._by_id[exp.handoff_id] = exp

    def get(self, handoff_id: str) -> Optional[RoutingExplanation]:
        with self._lock:
            return self._by_id.get(handoff_id)

    def all(self) -> Sequence[RoutingExplanation]:
        with self._lock:
            return [self._by_id[h] for h in self._order]

    def ids(self) -> Sequence[str]:
        with self._lock:
            return list(self._order)

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_id)

    def __contains__(self, handoff_id: object) -> bool:
        with self._lock:
            return handoff_id in self._by_id

    def as_mapping(self) -> Mapping[str, RoutingExplanation]:
        """Return a frozen point-in-time snapshot Mapping.

        Subsequent put() calls do NOT update the returned mapping.
        """
        with self._lock:
            return _MappingView(self._by_id, self._order)


class JsonlFileStore:
    """Append-only JSONL persistence.

    Each explanation is encoded as one JSON object per line, with a
    `__version__` field on every record so future schema migrations stay
    legible. On construction the file is read end-to-end and the index
    is rebuilt in memory; subsequent puts append to disk and update the
    index atomically under a lock.

    fsync is configurable. Default off because the typical use case is
    audit-after-the-fact -- losing the last few entries on crash is
    acceptable. Pass `fsync=True` for stricter durability.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: str, *, fsync: bool = False) -> None:
        self.path = path
        self._fsync = fsync
        self._lock = threading.Lock()
        self._by_id: Dict[str, RoutingExplanation] = {}
        self._order: List[str] = []
        self._load_existing()

    # ---- persistence --------------------------------------------------
    def _load_existing(self) -> None:
        # Track skipped-line counts so operators can see audit-log integrity
        # without having to grep the disk. Exposed via the `corruption_stats`
        # attribute after construction.
        skipped_json = 0
        skipped_shape = 0
        if not os.path.exists(self.path):
            # Make sure the parent directory exists so the first put() works.
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self.corruption_stats: Dict[str, int] = {
                "skipped_json": 0,
                "skipped_shape": 0,
            }
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as e:
                    # Skip corrupt lines rather than refuse to load -- a single
                    # truncated entry should not lock the whole audit log out.
                    skipped_json += 1
                    _log.warning(
                        "JsonlFileStore %s line %d: skipping non-JSON entry (%s)",
                        self.path,
                        lineno,
                        e,
                    )
                    continue
                exp = _deserialize(raw)
                if exp is None:
                    skipped_shape += 1
                    _log.warning(
                        "JsonlFileStore %s line %d: skipping entry with no recoverable identity",
                        self.path,
                        lineno,
                    )
                    continue
                if exp.handoff_id not in self._by_id:
                    self._order.append(exp.handoff_id)
                self._by_id[exp.handoff_id] = exp
        self.corruption_stats = {
            "skipped_json": skipped_json,
            "skipped_shape": skipped_shape,
        }
        if skipped_json or skipped_shape:
            _log.warning(
                "JsonlFileStore %s loaded with %d JSON-skip and %d shape-skip lines",
                self.path,
                skipped_json,
                skipped_shape,
            )

    def put(self, exp: RoutingExplanation) -> None:
        encoded = json.dumps(_serialize(exp), ensure_ascii=True)
        with self._lock:
            if exp.handoff_id not in self._by_id:
                self._order.append(exp.handoff_id)
            self._by_id[exp.handoff_id] = exp
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(encoded + "\n")
                if self._fsync:
                    f.flush()
                    os.fsync(f.fileno())

    def get(self, handoff_id: str) -> Optional[RoutingExplanation]:
        with self._lock:
            return self._by_id.get(handoff_id)

    def all(self) -> Sequence[RoutingExplanation]:
        with self._lock:
            return [self._by_id[h] for h in self._order]

    def ids(self) -> Sequence[str]:
        with self._lock:
            return list(self._order)

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_id)

    def __contains__(self, handoff_id: object) -> bool:
        with self._lock:
            return handoff_id in self._by_id

    def as_mapping(self) -> Mapping[str, RoutingExplanation]:
        """Return a frozen point-in-time snapshot Mapping.

        Subsequent put() calls do NOT update the returned mapping.
        """
        with self._lock:
            return _MappingView(self._by_id, self._order)


# ---- serialization helpers --------------------------------------------

def _serialize(exp: RoutingExplanation) -> Dict[str, Any]:
    return {
        "__version__": JsonlFileStore.SCHEMA_VERSION,
        **exp.to_dict(),
    }


def _deserialize(raw: Dict[str, Any]) -> Optional[RoutingExplanation]:
    """Best-effort reconstruction. Unknown fields are ignored; missing
    optionals get their defaults. Returns None only when the payload is
    so malformed we cannot recover an identity."""
    try:
        handoff_id = raw["handoff_id"]
        alternatives = [
            RouteCandidate(
                agent=c["agent"],
                skill_id=c["skill_id"],
                score=float(c.get("score", 0.0)),
                semantic=float(c.get("semantic", 0.0)),
                tag_overlap=float(c.get("tag_overlap", 0.0)),
                matched_tokens=list(c.get("matched_tokens", [])),
                matched_tags=list(c.get("matched_tags", [])),
                rejected_reason=c.get("rejected_reason"),
            )
            for c in raw.get("alternatives", [])
        ]
        return RoutingExplanation(
            handoff_id=handoff_id,
            task=raw.get("task", ""),
            intent=raw.get("intent", ""),
            chosen_agent=raw.get("chosen_agent"),
            chosen_skill=raw.get("chosen_skill"),
            rationale=raw.get("rationale", ""),
            features_fired=dict(raw.get("features_fired", {})),
            alternatives=alternatives,
            confidence=float(raw.get("confidence", 0.0)),
            agent_self_caveats=list(raw.get("agent_self_caveats", [])),
            blocked_reason=raw.get("blocked_reason"),
            prior_chain=list(raw.get("prior_chain", [])),
            assumptions=list(raw.get("assumptions", [])),
            margin=float(raw.get("margin", 0.0)),
            flags=tuple(raw.get("flags", ())),
        )
    except (KeyError, TypeError, ValueError):
        return None
