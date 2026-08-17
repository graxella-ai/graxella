"""A2A dispatch.

Sends a task to a chosen agent over the A2A protocol (JSON-RPC 2.0,
`message/send` method). The transport is intentionally pluggable so users
can inject a fake transport in tests or wire a custom client.

Spec: https://a2aproject.github.io/A2A/specification/  (Transport)
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, Optional, Protocol

from .exceptions import DispatchError


class Transport(Protocol):
    def send(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover - protocol
        ...


class HttpTransport:
    """Default JSON-RPC over HTTP transport. Uses httpx if available."""

    def __init__(self, *, timeout: float = 30.0):
        self.timeout = timeout

    def send(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import httpx  # type: ignore

            with httpx.Client(timeout=self.timeout, follow_redirects=True) as c:
                resp = c.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except ImportError:
            import urllib.request

            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                raise DispatchError(f"HTTP dispatch failed: {e}") from e
        except Exception as e:
            raise DispatchError(f"HTTP dispatch failed: {e}") from e


# Local transport: used by adapters that wrap a native (in-process) agent.
LocalHandler = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class LocalTransport:
    """In-process transport for native-wrapped agents (no HTTP)."""

    def __init__(self) -> None:
        self._handlers: Dict[str, LocalHandler] = {}

    def register(self, url: str, handler: LocalHandler) -> None:
        self._handlers[url] = handler

    def has(self, url: str) -> bool:
        """Public membership check used by CompositeTransport.

        Avoids reaching into the private `_handlers` dict from another module.
        """
        return url in self._handlers

    def send(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        handler = self._handlers.get(url)
        if handler is None:
            raise DispatchError(f"no local handler registered for url {url!r}")
        try:
            return handler(url, payload)
        except DispatchError:
            # Already wrapped -- propagate without double-wrap.
            raise
        except Exception as e:
            # Handlers raised by adapter authors are user code; convert any
            # unexpected error into a DispatchError so the mesh's retry/fail
            # path can react instead of seeing a bare TypeError or KeyError.
            raise DispatchError(
                f"local handler for {url!r} raised {e.__class__.__name__}: {e}"
            ) from e


class CompositeTransport:
    """Tries local handlers first, then falls back to HTTP."""

    def __init__(self, local: LocalTransport, http: Optional[Transport] = None):
        self.local = local
        self.http = http or HttpTransport()

    def send(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.local.has(url):
            return self.local.send(url, payload)
        return self.http.send(url, payload)


def build_message_send_payload(
    *,
    task: str,
    skill_id: Optional[str] = None,
    role: str = "user",
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct an A2A `message/send` JSON-RPC payload."""
    msg_id = message_id or uuid.uuid4().hex
    params: Dict[str, Any] = {
        "message": {
            "role": role,
            "messageId": msg_id,
            "parts": [{"kind": "text", "text": task}],
        }
    }
    if skill_id:
        params["message"]["metadata"] = {"agent2society.skill": skill_id}
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "message/send",
        "params": params,
    }


def extract_text(response: Dict[str, Any]) -> str:
    """Best-effort extraction of human-readable text from an A2A response.

    This must never raise on a malformed transport response -- the
    dispatch path treats the return value as the canonical agent reply,
    and a crash here would mask the real upstream error. We progressively
    fall back to repr() if everything else fails.
    """
    if not isinstance(response, dict):
        return str(response)
    if "error" in response and response["error"]:
        err = response["error"]
        if isinstance(err, dict):
            return f"[error] {err.get('message', err)}"
        return f"[error] {err}"
    result = response.get("result", response)
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        # Common shapes: {kind: "message", parts: [{kind: "text", text: "..."}]}
        parts = result.get("parts")
        if not parts and isinstance(result.get("message"), dict):
            parts = result["message"].get("parts")
        if isinstance(parts, list):
            chunks = []
            for p in parts:
                if isinstance(p, dict):
                    if "text" in p:
                        chunks.append(str(p["text"]))
                    elif p.get("kind") == "text" and "content" in p:
                        chunks.append(str(p["content"]))
            if chunks:
                return "\n".join(chunks)
        for key in ("text", "output", "content"):
            if key in result and isinstance(result[key], str):
                return result[key]
    # Final fallback: prefer JSON for legibility, but fall back to repr()
    # if `result` contains non-JSON-serialisable values (e.g. a Decimal
    # or a custom object some upstream framework slipped through).
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        return repr(result)
