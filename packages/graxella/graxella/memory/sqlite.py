"""SqliteExperienceStore — durable Experience storage in one file.

Implements the ExperienceStore Protocol from graxella.memory.episode. One
SQLite file per deployment (default: ./experience.db); rows are Episodes,
JSON columns hold the list-shaped fields. Indexed on the four dimensions
the hidden-agenda miners actually query: session_id, intent, agent_uri,
started_at.

Concurrency: one shared connection with a lock. Fine for the callback path
(one writer per graph run) and for the offline runner (single reader).
Multi-writer scenarios upgrade to Postgres, not this class.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any

from graxella.memory.episode import ExperienceEpisode, ToolCall

_JSON_LIST_COLS = ("routing_flags", "context_ids", "evidence_ids", "decisions")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id                 TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    envelope_id        TEXT NOT NULL,
    intent             TEXT NOT NULL,
    task               TEXT NOT NULL,
    skill              TEXT,
    side_effect_class  TEXT NOT NULL,
    agent_uri          TEXT NOT NULL,
    routing_margin     REAL,
    routing_flags      TEXT NOT NULL DEFAULT '[]',
    tool_calls         TEXT NOT NULL DEFAULT '[]',
    context_ids        TEXT NOT NULL DEFAULT '[]',
    evidence_ids       TEXT NOT NULL DEFAULT '[]',
    decisions          TEXT NOT NULL DEFAULT '[]',
    ok                 INTEGER NOT NULL,
    err                TEXT,
    latency_ms         REAL NOT NULL DEFAULT 0,
    retries            INTEGER NOT NULL DEFAULT 0,
    started_at         REAL NOT NULL,
    ended_at           REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_episodes_session   ON episodes(session_id);
CREATE INDEX IF NOT EXISTS ix_episodes_intent    ON episodes(intent);
CREATE INDEX IF NOT EXISTS ix_episodes_agent_uri ON episodes(agent_uri);
CREATE INDEX IF NOT EXISTS ix_episodes_started   ON episodes(started_at);
"""


class SqliteExperienceStore:
    """Durable ExperienceStore. One SQLite file, thread-safe via a mutex."""

    def __init__(self, path: str | Path = "./experience.db") -> None:
        self.path = str(Path(path))
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ write
    def put(self, episode: ExperienceEpisode) -> None:
        row = _episode_to_row(episode)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        sql = f"INSERT OR REPLACE INTO episodes ({cols}) VALUES ({placeholders})"
        with self._lock:
            self._conn.execute(sql, row)

    # ------------------------------------------------------------------- read
    def get(self, episode_id: str) -> ExperienceEpisode | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM episodes WHERE id = ?", (episode_id,)
            ).fetchone()
        return _row_to_episode(row) if row else None

    def by_session(self, session_id: str) -> list[ExperienceEpisode]:
        return self._select("session_id = ?", (session_id,))

    def by_intent(self, intent: str) -> list[ExperienceEpisode]:
        return self._select("intent = ?", (intent,))

    def all(self) -> list[ExperienceEpisode]:
        return self._select("1 = 1", ())

    # ------------------------------------------------------------------ close
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------- internals
    def _select(self, where: str, params: tuple[Any, ...]) -> list[ExperienceEpisode]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM episodes WHERE {where} ORDER BY started_at",
                params,
            ).fetchall()
        return [_row_to_episode(r) for r in rows]


# ---------------------------------------------------------------- serializers
def _episode_to_row(e: ExperienceEpisode) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": e.id,
        "session_id": e.session_id,
        "envelope_id": e.envelope_id,
        "intent": e.intent,
        "task": e.task,
        "skill": e.skill,
        "side_effect_class": e.side_effect_class,
        "agent_uri": e.agent_uri,
        "routing_margin": e.routing_margin,
        "routing_flags": json.dumps(list(e.routing_flags)),
        "tool_calls": json.dumps([_tc_to_json(tc) for tc in e.tool_calls]),
        "context_ids": json.dumps(list(e.context_ids)),
        "evidence_ids": json.dumps(list(e.evidence_ids)),
        "decisions": json.dumps(list(e.decisions)),
        "ok": 1 if e.ok else 0,
        "err": e.err,
        "latency_ms": e.latency_ms,
        "retries": e.retries,
        "started_at": e.started_at,
        "ended_at": e.ended_at,
    }
    return row


def _row_to_episode(row: sqlite3.Row) -> ExperienceEpisode:
    payload = {k: row[k] for k in row.keys()}
    payload["ok"] = bool(payload["ok"])
    for col in _JSON_LIST_COLS:
        payload[col] = json.loads(payload[col] or "[]")
    payload["tool_calls"] = [
        ToolCall(**tc) for tc in json.loads(payload["tool_calls"] or "[]")
    ]
    valid = {f.name for f in dc_fields(ExperienceEpisode)}
    return ExperienceEpisode(**{k: v for k, v in payload.items() if k in valid})


def _tc_to_json(tc: ToolCall) -> dict[str, Any]:
    return {
        "tool_id": tc.tool_id,
        "args_hash": tc.args_hash,
        "ok": tc.ok,
        "latency_ms": tc.latency_ms,
        "err": tc.err,
    }
