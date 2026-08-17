"""agent2society orchestrator over real A2A agents.

This client talks to the SAME A2A servers as the a2a_only sibling.
The agent server files in `agents/` are not modified. Only this client
changes -- it imports agent2society, wraps the discovered A2A cards in
a Society, and gets routing + explanations + governance for free.

Proof point: a developer using A2A today can add this file (and remove
their hand-rolled supervisor) and get a transparency layer with no
edits to their agent code.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from metrics import execution_tokens

from agent2society import Society, Handoff


# ---------------------------------------------------------------------------
# Custom transport: bridges agent2society's payload to a2a-sdk's wire format.
#
# agent2society defaults to the public A2A spec method `message/send`.
# Google's a2a-sdk 1.1.0 names the same JSON-RPC method `SendMessage` and
# requires an `A2A-Version: 1.0` header. This 30-line transport adapts
# one to the other -- nothing more.
# ---------------------------------------------------------------------------

class A2ASDKTransport:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def send(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        text = ""
        try:
            text = payload["params"]["message"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            text = str(payload)

        body = {
            "jsonrpc": "2.0",
            "id": payload.get("id", uuid.uuid4().hex),
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": uuid.uuid4().hex,
                    "role": "ROLE_USER",
                    "parts": [{"text": text}],
                },
                "configuration": {"returnImmediately": False},
            },
        }
        headers = {"A2A-Version": "1.0"}
        with httpx.Client(timeout=self.timeout) as h:
            r = h.post(url, json=body, headers=headers)
            r.raise_for_status()
            return r.json()


# ---------------------------------------------------------------------------
# Discovery: pull the SAME well-known endpoints the a2a_only sibling uses.
# Flatten supported_interfaces[0].url to top-level so agent2society's card
# parser accepts the A2A card schema directly.
# ---------------------------------------------------------------------------

AGENT_URLS = [
    "http://127.0.0.1:8101",
    "http://127.0.0.1:8102",
    "http://127.0.0.1:8103",
    "http://127.0.0.1:8104",
]


def discover_cards() -> List[Dict[str, Any]]:
    cards = []
    with httpx.Client(timeout=2.0) as h:
        for url in AGENT_URLS:
            r = h.get(f"{url}/.well-known/agent-card.json")
            r.raise_for_status()
            card = r.json()
            # agent2society expects a top-level `url`; the A2A v1.0 schema
            # carries it inside supported_interfaces[].url. Surface it.
            ifaces = card.get("supportedInterfaces") or card.get("supported_interfaces") or []
            if ifaces and isinstance(ifaces, list):
                card["url"] = ifaces[0].get("url", url)
            else:
                card["url"] = url
            cards.append(card)
    return cards


# ---------------------------------------------------------------------------
# Society builder -- 5 lines of orchestration code vs. the hand-rolled
# supervisor's 60+ lines. Plus you get explanations, governance, boundaries.
# ---------------------------------------------------------------------------

def build_society() -> "tuple[Society, list]":
    cards = discover_cards()
    alerts: list = []

    society = Society(transport=A2ASDKTransport(), strict=False, min_score=0.05)

    # Governance hooks (detection-only -- they cannot alter dispatch)
    society.on_low_margin(
        lambda exp: alerts.append(
            f"[LOW_MARGIN] {exp.chosen_agent} margin={exp.margin:.3f}"
        ),
        threshold=0.12,
    )
    society.on_low_confidence(
        lambda exp: alerts.append(
            f"[LOW_CONFIDENCE] {exp.chosen_agent} score={exp.confidence:.3f}"
        ),
        threshold=0.25,
    )

    for card in cards:
        society.add(card)

    # Hard boundary: PII tokens must not reach any agent.
    society.boundary(
        "KnowledgeBaseLookup",
        deny=["passport", "date of birth", "social security", "ssn"],
    )
    society.boundary(
        "EscalationHandler",
        deny=["passport", "date of birth", "social security", "ssn"],
    )
    society.boundary(
        "ResponseGenerator",
        deny=["passport", "date of birth", "social security", "ssn"],
    )
    society.boundary(
        "IntentClassifier",
        deny=["passport", "date of birth", "social security", "ssn"],
    )

    return society, alerts


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

@dataclass
class TicketResult:
    ticket_id: str
    text: str
    chosen_agent: Optional[str]
    expected: Optional[str]
    correct: bool
    reply: str
    elapsed_ms: float
    coord_in_tokens: int = 0   # always 0 -- TF-IDF routing burns no LLM tokens
    coord_out_tokens: int = 0
    exec_in_tokens: int = 0
    exec_out_tokens: int = 0
    rationale: str = ""
    margin: float = -1.0
    flags: List[str] = field(default_factory=list)


def run_pipeline(tickets: List[Dict[str, Any]]) -> "tuple[List[TicketResult], list]":
    society, alerts = build_society()
    results: List[TicketResult] = []
    for t in tickets:
        start = time.perf_counter()
        h = Handoff(task=t["text"])
        try:
            reply = society.run(h)
        except Exception as exc:
            reply = f"[ERR] {exc}"
        elapsed = (time.perf_counter() - start) * 1000

        exp = society.explain(h.id)
        chosen = exp.chosen_agent if exp is not None else None
        margin = exp.margin if exp is not None else -1.0
        rationale = exp.rationale if exp is not None else ""
        flags = list(exp.flags) if exp is not None and exp.flags else []

        ein, eout = (execution_tokens(t["text"]) if chosen is not None else (0, 0))
        results.append(TicketResult(
            ticket_id=t["id"], text=t["text"],
            chosen_agent=chosen, expected=t["expected"],
            correct=(chosen == t["expected"]) if t["expected"] else (chosen is None),
            reply=reply or "(no reply)", elapsed_ms=elapsed,
            coord_in_tokens=0, coord_out_tokens=0,
            exec_in_tokens=ein, exec_out_tokens=eout,
            rationale=rationale, margin=margin, flags=flags,
        ))
    return results, alerts
