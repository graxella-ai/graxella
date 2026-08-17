"""Phase 1 smoke test: boot ONE worker agent and call it via A2AClient with real Ollama."""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from comparisons.v3.server_manager import ServerManager


async def call(url: str, task: str, skill_id: str = "") -> dict:
    import httpx
    from a2a.client import ClientFactory, ClientConfig
    from a2a.types import SendMessageRequest, Message, Part, Role
    from google.protobuf.struct_pb2 import Struct
    from google.protobuf.json_format import ParseDict, MessageToDict

    async with httpx.AsyncClient(timeout=120.0) as httpx_client:
        factory = ClientFactory(ClientConfig(streaming=False, httpx_client=httpx_client))
        client = await factory.create_from_url(url)
        meta = Struct()
        if skill_id:
            ParseDict({"skill_id": skill_id}, meta)
        req = SendMessageRequest(
            message=Message(
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_USER,
                parts=[Part(text=task)],
                metadata=meta,
            )
        )
        text = ""
        ret_meta = {}
        async for resp in client.send_message(req):
            if resp.HasField("message"):
                for p in resp.message.parts:
                    if p.text:
                        text += p.text
                if resp.message.metadata:
                    ret_meta = MessageToDict(resp.message.metadata) or {}
            elif resp.HasField("task"):
                for m in resp.task.history:
                    if m.role == Role.ROLE_AGENT:
                        for p in m.parts:
                            if p.text:
                                text += p.text
                        if m.metadata:
                            ret_meta = MessageToDict(m.metadata) or {}
        await client.close()
        return {"text": text, "meta": ret_meta}


def main() -> int:
    os.environ.setdefault("V3_MODEL", "qwen2.5:0.5b")  # fast for smoke
    with ServerManager(agents=["market_data_agent"], startup_timeout=120.0) as mgr:
        url = mgr.agent_urls()["market_data_agent"]
        t0 = time.perf_counter()
        result = asyncio.run(call(url, "Fetch Apple's last 12 quarters of price data.", skill_id="fetch_prices"))
        elapsed = (time.perf_counter() - t0) * 1000.0
    print(f"[worker_smoke] elapsed_ms={elapsed:.0f}")
    print(f"[worker_smoke] text={result['text'][:240]!r}")
    print(f"[worker_smoke] meta={result['meta']}")
    if not result["text"]:
        print("[worker_smoke] FAIL: empty text"); return 1
    if int(result["meta"].get("agent_input_tokens", 0)) <= 0:
        print("[worker_smoke] FAIL: no token count"); return 1
    print("[worker_smoke] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
