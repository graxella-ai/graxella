"""graxella.beliefs.buffer — the durable evidence write buffer (task 3-1).

The hot path's remaining latency was two synchronous SQLite writes per
dispatch. This buffer takes them off the dispatch path without losing
crash-safety:

  * hot path      — append one JSON line to a local WAL file (~0.05 ms)
                    and enqueue for the background flusher. Assertion
                    ids are generated HERE, so decision→outcome links
                    hold before anything reaches SQLite.
  * background    — a daemon thread drains the queue into the mnema
                    store via observe(assertion_id=...), advancing a
                    cursor file after each flushed line.
  * crash         — kill the process at any point: lines past the
                    cursor replay into the store on the next startup.
                    (Durability boundary: OS file buffers — a machine
                    power loss between write and OS flush can lose the
                    tail; documented, not hidden.)
  * reads         — any read through Memory drains the queue first, so
                    readers never see a ledger behind their own writes.
"""
from __future__ import annotations

import json
import queue
import threading
import uuid
from pathlib import Path
from typing import Any


class WalBuffer:
    """Durable, ordered, replay-on-start write buffer for one MnemaClient."""

    def __init__(self, client: Any, wal_path: str | Path, *,
                 autostart: bool = True) -> None:
        self.client = client
        self.wal_path = Path(wal_path)
        self.cursor_path = self.wal_path.with_suffix(".cursor")
        self.wal_path.parent.mkdir(parents=True, exist_ok=True)
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._wal_file = self.wal_path.open("a", encoding="utf-8")
        self._recover()
        self._worker: threading.Thread | None = None
        if autostart:
            self.start()

    # -- hot path -------------------------------------------------------------

    def observe(self, statement: str, **kwargs: Any) -> str:
        """WAL-append + enqueue. Returns the (pre-generated) assertion id."""
        aid = kwargs.pop("assertion_id", None) or f"asr_{uuid.uuid4().hex}"
        entry = {"assertion_id": aid, "statement": statement, **kwargs}
        line = json.dumps(entry, sort_keys=True, default=str)
        with self._lock:
            self._wal_file.write(line + "\n")
            self._wal_file.flush()
        self._idle.clear()
        self._q.put(entry)
        return aid

    # -- background flusher ---------------------------------------------------

    def start(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._stop.clear()
            self._worker = threading.Thread(target=self._run, daemon=True,
                                            name="graxella-wal-flusher")
            self._worker.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                entry = self._q.get(timeout=0.2)
            except queue.Empty:
                self._idle.set()
                continue
            try:
                self._apply(entry)
            finally:
                self._advance_cursor()
                self._q.task_done()
                if self._q.empty():
                    self._idle.set()

    def _apply(self, entry: dict) -> None:
        e = dict(entry)
        stmt = e.pop("statement")
        e["derived_from"] = tuple(e.get("derived_from") or ())
        self.client.observe(stmt, **e)

    # -- consistency ----------------------------------------------------------

    def drain(self, timeout: float = 30.0) -> None:
        """Block until every enqueued write reached the store.

        Uses ``Queue.join()`` — the put happened-before this call on the
        writing thread, so join() cannot return before that write is
        applied. (An idle-Event version had a set/clear race that let
        reads slip past queued writes.)"""
        del timeout
        if self._worker is None or not self._worker.is_alive():
            # No flusher (crashed / autostart=False): apply inline.
            while not self._q.empty():
                self._apply(self._q.get_nowait())
                self._advance_cursor()
            return
        self._q.join()

    @property
    def pending(self) -> int:
        return self._q.qsize()

    def close(self) -> None:
        self.drain()
        self._stop.set()
        with self._lock:
            self._wal_file.close()

    # -- durability -----------------------------------------------------------

    def _cursor(self) -> int:
        try:
            return int(self.cursor_path.read_text())
        except (FileNotFoundError, ValueError):
            return 0

    def _advance_cursor(self) -> None:
        self.cursor_path.write_text(str(self._cursor() + 1))

    def _recover(self) -> None:
        """Replay WAL lines past the cursor into the store — the reason a
        kill -9 mid-run loses nothing that reached the WAL."""
        if not self.wal_path.exists():
            return
        lines = [ln for ln in
                 self.wal_path.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
        cursor = self._cursor()
        recovered = lines[cursor:]
        for ln in recovered:
            self._apply(json.loads(ln))
            self._advance_cursor()
        if recovered:
            import logging
            logging.getLogger("graxella").info(
                "graxella: recovered %d unflushed ledger writes from %s",
                len(recovered), self.wal_path)


__all__ = ["WalBuffer"]
