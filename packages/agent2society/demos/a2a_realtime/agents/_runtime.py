"""Shared a2a-sdk server bootstrap.

Every agent module reuses `build_app(card, executor)` and
`serve_in_thread(app, port)`. This keeps the per-agent files focused on
domain logic only -- no transport boilerplate leaks into them.
"""
from __future__ import annotations

import threading
import time
from typing import Tuple

import httpx
import uvicorn
from fastapi import FastAPI

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import AgentCard


def build_app(card: AgentCard, executor: AgentExecutor) -> FastAPI:
    handler = DefaultRequestHandler(executor, InMemoryTaskStore(), card)
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )
    return app


def serve_in_thread(app: FastAPI, port: int, timeout: float = 5.0) -> Tuple[uvicorn.Server, threading.Thread]:
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(
                f"http://127.0.0.1:{port}/.well-known/agent-card.json",
                timeout=0.5,
            )
            if r.status_code == 200:
                return server, t
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError(f"server on port {port} did not come up")
