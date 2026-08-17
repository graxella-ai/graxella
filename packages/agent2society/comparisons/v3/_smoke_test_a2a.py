"""Phase 0 smoke test: prove the a2a-sdk v1.1.0 server + client round-trip works.

Spawns a tiny echo agent on port 5099 in a subprocess, polls
/.well-known/agent-card.json until healthy, sends a real JSON-RPC message,
asserts the echoed text comes back.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import sys
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

# Make sure src/ is importable when running directly
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run_echo_server(port: int) -> None:
    """Subprocess entrypoint: run a dummy A2A echo agent on `port`."""
    import uvicorn
    from fastapi import FastAPI
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.events import EventQueue
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import (
        create_agent_card_routes,
        create_jsonrpc_routes,
    )
    from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import (
        AgentCard,
        AgentSkill,
        AgentCapabilities,
        AgentInterface,
        Message,
        Part,
        Role,
    )
    import uuid

    class EchoExecutor(AgentExecutor):
        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            user_text = ""
            if context.message is not None:
                for p in context.message.parts:
                    if p.text:
                        user_text += p.text
            reply = Message(
                message_id=str(uuid.uuid4()),
                context_id=context.context_id or "",
                role=Role.ROLE_AGENT,
                parts=[Part(text=f"echo: {user_text}")],
            )
            await event_queue.enqueue_event(reply)

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            return None

    card = AgentCard(
        name="echo_agent",
        description="Tiny echo agent for smoke test",
        version="0.0.1",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supported_interfaces=[
            AgentInterface(
                url=f"http://localhost:{port}/",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        skills=[
            AgentSkill(
                id="echo",
                name="echo",
                description="Echoes the user input back",
                tags=["test"],
            )
        ],
    )

    app = FastAPI()
    handler = DefaultRequestHandler(
        agent_executor=EchoExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _wait_for_card(port: int, timeout: float = 30.0) -> None:
    url = f"http://localhost:{port}/.well-known/agent-card.json"
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as r:
                if r.status == 200:
                    return
        except Exception as exc:
            last_err = exc
        time.sleep(0.5)
    raise RuntimeError(f"agent on :{port} did not come up: {last_err}")


async def _call_via_client(port: int) -> str:
    from a2a.client import ClientFactory, ClientConfig
    from a2a.types import SendMessageRequest, Message, Part, Role
    import uuid

    async with httpx.AsyncClient(timeout=30.0) as httpx_client:
        factory = ClientFactory(
            ClientConfig(streaming=False, httpx_client=httpx_client)
        )
        client = await factory.create_from_url(f"http://localhost:{port}")
        req = SendMessageRequest(
            message=Message(
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_USER,
                parts=[Part(text="hello world")],
            )
        )
        out_text = ""
        async for resp in client.send_message(req):
            if resp.HasField("message"):
                for p in resp.message.parts:
                    if p.text:
                        out_text += p.text
            elif resp.HasField("task"):
                # if a Task arrives, look at history
                for m in resp.task.history:
                    if m.role == Role.ROLE_AGENT:
                        for p in m.parts:
                            if p.text:
                                out_text += p.text
        await client.close()
        return out_text


def main() -> int:
    port = 5099
    proc = multiprocessing.Process(target=_run_echo_server, args=(port,), daemon=True)
    proc.start()
    try:
        _wait_for_card(port)
        print(f"[smoke] echo server is up on :{port}")
        result = asyncio.run(_call_via_client(port))
        print(f"[smoke] client received: {result!r}")
        assert "echo: hello world" in result, result
        print("[smoke] PASS")
        return 0
    except Exception as exc:
        print(f"[smoke] FAIL: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        proc.terminate()
        proc.join(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
