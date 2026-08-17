"""GraxellaCallback — turn a LangGraph run into one ExperienceEpisode.

Attach to any LangChain/LangGraph app via `config={"callbacks": [cb]}`.
One root chain run → one Episode. Every tool call within that run becomes
a ToolCall on the Episode. Tool errors go on the ToolCall's `err` field;
a chain error marks the whole Episode `ok=False`.

Design:
  * ROOT chain (parent_run_id is None) opens an Episode.
  * Nested chains inherit the root's Episode (recorded via `decisions`).
  * Every tool run attaches to the nearest ancestor Episode by walking
    parent_run_id up the tree.
  * on_chain_end/error closes the Episode and persists to the store.

Failure isolation: the callback never raises into the customer's graph.
A bug here must degrade to "no Episode recorded", never to "graph crashed."
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from uuid import UUID

from graxella.memory.episode import ExperienceEpisode, ExperienceStore, ToolCall

try:
    from langchain_core.callbacks import BaseCallbackHandler  # type: ignore
    _HAS_LC = True
except Exception:  # pragma: no cover - optional dep
    _HAS_LC = False

    class BaseCallbackHandler:  # type: ignore[no-redef]
        """Fallback stub so the module imports without langchain_core."""


def _args_hash(payload: Any) -> str:
    try:
        canon = json.dumps(payload, sort_keys=True, default=str)
    except TypeError:
        canon = repr(payload)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


class GraxellaCallback(BaseCallbackHandler):
    """LangChain callback that materialises Experience rows in `store`.

    Usage:

        cb = GraxellaCallback(store=SqliteExperienceStore("./exp.db"))
        result = app.invoke(state, config={"callbacks": [cb]})
    """

    # These flags matter for how LangChain routes events to us.
    raise_error: bool = False
    run_inline: bool = True

    def __init__(self, store: ExperienceStore, *,
                 session_id: str | None = None,
                 default_intent: str = "chain_run",
                 default_side_effect: str = "read_only") -> None:
        self.store = store
        self.session_id = session_id or f"lg-{int(time.time() * 1000)}"
        self.default_intent = default_intent
        self.default_side_effect = default_side_effect
        # run_id -> Episode (only root chains own one)
        self._episodes: dict[str, ExperienceEpisode] = {}
        # run_id -> (parent_run_id or None) for ancestor walks
        self._parents: dict[str, str | None] = {}
        # tool run_id -> (start_ts, tool_name, input_repr)
        self._tool_starts: dict[str, tuple[float, str, str]] = {}

    # ------------------------------------------------ ancestor lookup helpers
    def _find_root(self, run_id: str) -> str | None:
        cur: str | None = run_id
        seen: set[str] = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            if cur in self._episodes:
                return cur
            cur = self._parents.get(cur)
        return None

    def _safe_str(self, run_id: UUID | str | None) -> str | None:
        return str(run_id) if run_id is not None else None

    # -------------------------------------------------------------- chain in
    def on_chain_start(self, serialized: dict[str, Any] | None,
                       inputs: dict[str, Any] | Any,
                       *, run_id: UUID,
                       parent_run_id: UUID | None = None,
                       tags: list[str] | None = None,
                       metadata: dict[str, Any] | None = None,
                       **kwargs: Any) -> None:
        try:
            rid = str(run_id)
            self._parents[rid] = self._safe_str(parent_run_id)

            # Only ROOT chains own an Episode. Nested chains inherit.
            if parent_run_id is not None:
                return

            name = (serialized or {}).get("name") or (metadata or {}).get("name") \
                   or kwargs.get("name") or "chain"
            task_repr = _summarise(inputs)

            episode = ExperienceEpisode(
                session_id=self.session_id,
                envelope_id=rid,
                intent=(metadata or {}).get("intent", self.default_intent),
                task=task_repr,
                skill=name,
                side_effect_class=(metadata or {}).get(
                    "side_effect_class", self.default_side_effect),
                started_at=time.time(),
            )
            self._episodes[rid] = episode
        except Exception:
            pass  # Never break the customer's graph.

    # ------------------------------------------------------------- chain out
    def on_chain_end(self, outputs: dict[str, Any] | Any,
                     *, run_id: UUID,
                     parent_run_id: UUID | None = None,
                     **kwargs: Any) -> None:
        self._close_chain(str(run_id), ok=True, err=None)

    def on_chain_error(self, error: BaseException,
                       *, run_id: UUID,
                       parent_run_id: UUID | None = None,
                       **kwargs: Any) -> None:
        self._close_chain(str(run_id), ok=False, err=repr(error))

    def _close_chain(self, rid: str, *, ok: bool, err: str | None) -> None:
        try:
            episode = self._episodes.pop(rid, None)
            self._parents.pop(rid, None)
            if episode is None:
                return  # nested chain; nothing to persist directly

            episode.ended_at = time.time()
            episode.latency_ms = (episode.ended_at - episode.started_at) * 1000
            # An episode is ok only if the chain succeeded AND every tool did.
            all_tools_ok = all(tc.ok for tc in episode.tool_calls)
            episode.ok = ok and all_tools_ok
            episode.err = err
            self.store.put(episode)
        except Exception:
            pass

    # --------------------------------------------------------------- tool in
    def on_tool_start(self, serialized: dict[str, Any] | None,
                      input_str: str,
                      *, run_id: UUID,
                      parent_run_id: UUID | None = None,
                      tags: list[str] | None = None,
                      metadata: dict[str, Any] | None = None,
                      inputs: dict[str, Any] | None = None,
                      **kwargs: Any) -> None:
        try:
            rid = str(run_id)
            self._parents[rid] = self._safe_str(parent_run_id)
            name = (serialized or {}).get("name") or kwargs.get("name") \
                   or "unknown_tool"
            payload = json.dumps(inputs, default=str) if inputs is not None \
                      else (input_str or "")
            self._tool_starts[rid] = (time.time(), name, payload)
        except Exception:
            pass

    # -------------------------------------------------------------- tool out
    def on_tool_end(self, output: Any,
                    *, run_id: UUID,
                    parent_run_id: UUID | None = None,
                    **kwargs: Any) -> None:
        self._finish_tool(str(run_id), ok=True, err=None)

    def on_tool_error(self, error: BaseException,
                      *, run_id: UUID,
                      parent_run_id: UUID | None = None,
                      **kwargs: Any) -> None:
        self._finish_tool(str(run_id), ok=False, err=repr(error))

    def _finish_tool(self, rid: str, *, ok: bool, err: str | None) -> None:
        try:
            start_info = self._tool_starts.pop(rid, None)
            if start_info is None:
                self._parents.pop(rid, None)
                return
            started, name, payload = start_info
            latency_ms = (time.time() - started) * 1000
            # Walk parents BEFORE popping — otherwise the chain is broken.
            root_rid = self._find_root(rid)
            self._parents.pop(rid, None)
            episode = self._episodes.get(root_rid) if root_rid else None
            if episode is None:
                return  # tool ran outside any chain we're tracking
            episode.tool_calls.append(ToolCall(
                tool_id=name,
                args_hash=_args_hash(payload),
                ok=ok,
                latency_ms=latency_ms,
                err=err,
            ))
            episode.decisions.append(f"tool::{name}::{'ok' if ok else 'err'}")
        except Exception:
            pass


def _summarise(inputs: Any, max_len: int = 240) -> str:
    """Shrink chain inputs into a printable task string for the Episode."""
    try:
        if isinstance(inputs, dict):
            for key in ("input", "task", "question", "query", "messages"):
                if key in inputs:
                    return _summarise(inputs[key], max_len)
        if isinstance(inputs, (list, tuple)) and inputs:
            return _summarise(inputs[-1], max_len)
        if isinstance(inputs, str):
            return inputs[:max_len]
        text = json.dumps(inputs, default=str)
    except Exception:
        text = repr(inputs)
    return text[:max_len]
