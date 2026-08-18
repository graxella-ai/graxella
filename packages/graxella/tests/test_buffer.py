"""Task 3-1 — the durable evidence write buffer."""
from __future__ import annotations

import time

import pytest

from graxella.beliefs import Memory
from graxella.beliefs.buffer import WalBuffer


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "m.db"), agent_id="buf",
                         namespace="refunds", buffered=True)


def test_ids_are_stable_before_flush(memory):
    """decision→outcome linking works even while writes are queued."""
    aid = memory.record_decision(decision_type="delegate", task="t",
                                 chosen="a::s")
    oid = memory.record_outcome(decision_id=aid, ok=True)
    assert aid.startswith("asr_") and oid.startswith("asr_")
    rows = memory.beliefs(subject=aid, predicate="outcome")  # drains first
    assert rows[0]["id"] == oid
    assert aid in rows[0]["derived_from"]


def test_read_barrier_never_lags_writes(memory):
    for i in range(25):
        aid = memory.record_decision(decision_type="delegate",
                                     task=f"task {i}", chosen="a::s")
        memory.record_outcome(decision_id=aid, ok=True)
    assert memory.outcome_stats()["total"]["count"] == 25


def test_crash_recovery_replays_unflushed_wal(tmp_path):
    """kill -9 semantics: WAL lines past the cursor replay on startup."""
    m1 = Memory.sqlite(str(tmp_path / "m.db"), agent_id="buf",
                       namespace="refunds", buffered=True)
    # Simulate the crash: stop the flusher, write, never drain.
    m1._buffer._stop.set()
    time.sleep(0.3)
    aid = m1.record_decision(decision_type="delegate", task="pre-crash",
                             chosen="a::s")
    m1.record_outcome(decision_id=aid, ok=False, err="crashed mid-run")
    assert m1._client.beliefs(subject=aid) == []      # store never saw it
    m1._buffer._wal_file.close()

    # "Restart": a fresh Memory over the same paths recovers the WAL.
    m2 = Memory.sqlite(str(tmp_path / "m.db"), agent_id="buf",
                       namespace="refunds", buffered=True)
    rows = m2.beliefs(subject=aid, predicate="outcome")
    assert len(rows) == 1
    assert "crashed mid-run" in rows[0]["statement"]


def test_recovery_is_idempotent(tmp_path):
    """A second restart replays nothing — the cursor advanced."""
    m1 = Memory.sqlite(str(tmp_path / "m.db"), agent_id="buf", buffered=True)
    aid = m1.record_decision(decision_type="delegate", task="t", chosen="a")
    m1._buffer.close()

    m2 = Memory.sqlite(str(tmp_path / "m.db"), agent_id="buf", buffered=True)
    m3_count = len(m2.beliefs(subject=f"decision::delegate::a"))
    m2._buffer.close()
    m3 = Memory.sqlite(str(tmp_path / "m.db"), agent_id="buf", buffered=True)
    assert len(m3.beliefs(subject=f"decision::delegate::a")) == m3_count == 1


def test_buffered_write_is_fast(memory):
    """The hot-path write is a WAL append, not a SQLite transaction."""
    t0 = time.perf_counter()
    n = 50
    for i in range(n):
        memory.record_decision(decision_type="delegate", task=f"t{i}",
                               chosen="a::s")
    per_write_ms = (time.perf_counter() - t0) * 1000 / n
    assert per_write_ms < 2.0, f"buffered write {per_write_ms:.2f}ms"
