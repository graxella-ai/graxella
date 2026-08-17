from __future__ import annotations

import agent2society as r2r
from agent2society.dispatcher import (
    build_message_send_payload,
    extract_text,
)


def test_build_payload_is_a2a_shaped():
    p = build_message_send_payload(task="hi", skill_id="s1")
    assert p["jsonrpc"] == "2.0"
    assert p["method"] == "message/send"
    msg = p["params"]["message"]
    assert msg["role"] == "user"
    assert msg["parts"][0]["text"] == "hi"
    assert msg["metadata"]["agent2society.skill"] == "s1"


def test_extract_text_from_message_shape():
    resp = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "role": "agent",
            "parts": [{"kind": "text", "text": "hello back"}],
        },
    }
    assert extract_text(resp) == "hello back"


def test_extract_text_handles_error():
    resp = {"jsonrpc": "2.0", "id": "1", "error": {"code": -1, "message": "boom"}}
    assert "[error]" in extract_text(resp)
