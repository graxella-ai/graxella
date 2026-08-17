"""Pure-A2A orchestrator: hand-rolled supervisor.

What you have to write yourself when you only have the A2A protocol and
no orchestration layer:

  1. Discovery loop -- pull each agent's card from its well-known URL.
  2. Routing function -- pick which agent handles each ticket.
  3. Dispatch loop -- build SendMessageRequest, await response, extract text.
  4. Result table -- you can record what you decided, but nothing tells
     you WHY this agent over the others, what the margin was, whether
     a boundary was crossed, or whether the choice was confident.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from a2a.client import create_client, ClientConfig
from a2a.helpers.proto_helpers import get_message_text, new_text_message
from a2a.types import SendMessageRequest, SendMessageConfiguration

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from metrics import supervisor_coord_tokens, execution_tokens


# ---------------------------------------------------------------------------
# Step 1: discover cards (the developer has to know the URLs themselves)
# ---------------------------------------------------------------------------

AGENT_URLS = [
    "http://127.0.0.1:8101",   # IntentClassifier
    "http://127.0.0.1:8102",   # KnowledgeBaseLookup
    "http://127.0.0.1:8103",   # EscalationHandler
    "http://127.0.0.1:8104",   # ResponseGenerator
]


async def discover() -> List[Dict[str, Any]]:
    """Pull each agent's card and turn it into a routing record."""
    async with httpx.AsyncClient(timeout=2.0) as h:
        cards = []
        for url in AGENT_URLS:
            r = await h.get(f"{url}/.well-known/agent-card.json")
            r.raise_for_status()
            card = r.json()
            cards.append({"url": url, "card": card})
    return cards


# ---------------------------------------------------------------------------
# Step 2: routing function (this is what the developer has to write)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "for", "on", "by", "at",
    "is", "are", "that", "this", "with", "it", "as", "be", "from", "has",
    "have", "i", "you", "we", "my", "our", "your", "me", "they",
}


def _bag(text: str) -> set:
    return {w for w in text.lower().split() if w not in _STOPWORDS}


def route_naive(ticket_text: str, cards: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the agent whose tags + description overlap most with the ticket.

    This is the kind of keyword-overlap routing a developer writes by hand
    when they don't have a routing layer. It works for clear cases and
    fails silently on ambiguous ones -- no margin, no explanation, no
    early warning.
    """
    bag = _bag(ticket_text)
    best = None
    best_score = 0
    for entry in cards:
        card = entry["card"]
        # Gather all keyword-ish text from the card
        words = set()
        words |= _bag(card.get("description", ""))
        for skill in card.get("skills", []):
            words |= _bag(skill.get("description", ""))
            for tag in skill.get("tags", []):
                words.add(tag.lower())
        score = len(words & bag)
        if score > best_score:
            best = entry
            best_score = score
    return best


# ---------------------------------------------------------------------------
# Step 3: dispatch (developer has to write the JSON-RPC send loop too)
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
    # token accounting -- supervisor burns these on every routing call
    coord_in_tokens: int = 0   # LLM supervisor: prompt tokens per routing call
    coord_out_tokens: int = 0  # LLM supervisor: output tokens per routing call
    exec_in_tokens: int = 0    # execution agent: input tokens
    exec_out_tokens: int = 0   # execution agent: output tokens
    # what's MISSING (the proof point)
    rationale: str = ""        # always empty -- nothing tells us why
    margin: float = -1.0       # always -1 -- nothing tells us how close it was
    flags: List[str] = field(default_factory=list)


async def dispatch(entry: Dict[str, Any], text: str) -> str:
    cfg = ClientConfig(streaming=False, polling=False)
    client = await create_client(entry["url"], cfg)
    try:
        req = SendMessageRequest(
            message=new_text_message(text=text),
            configuration=SendMessageConfiguration(return_immediately=False),
        )
        async for resp in client.send_message(req):
            if hasattr(resp, "HasField"):
                if resp.HasField("message"):
                    return get_message_text(resp.message)
                if resp.HasField("task") and resp.task.status.HasField("message"):
                    return get_message_text(resp.task.status.message)
        return "(no reply)"
    finally:
        await client.close()


async def run_pipeline(tickets: List[Dict[str, Any]]) -> List[TicketResult]:
    cards = await discover()
    results: List[TicketResult] = []
    for t in tickets:
        start = time.perf_counter()
        # Count coordination tokens exactly as a LangGraph LLM supervisor
        # would burn on every routing call (system prompt + ticket + JSON reply).
        # The routing decision itself is the same keyword overlap as before --
        # what changes is that we now *measure* the token cost rather than
        # pretending the developer's real supervisor is free.
        cin, cout = supervisor_coord_tokens(t["text"])
        choice = route_naive(t["text"], cards)
        if choice is None:
            elapsed = (time.perf_counter() - start) * 1000
            results.append(TicketResult(
                ticket_id=t["id"], text=t["text"],
                chosen_agent=None, expected=t["expected"],
                correct=(t["expected"] is None),
                reply="(no agent matched)", elapsed_ms=elapsed,
                coord_in_tokens=cin, coord_out_tokens=cout,
            ))
            continue
        try:
            reply = await dispatch(choice, t["text"])
        except Exception as exc:
            reply = f"[ERR] {exc}"
        elapsed = (time.perf_counter() - start) * 1000
        chosen_name = choice["card"]["name"]
        ein, eout = execution_tokens(t["text"])
        results.append(TicketResult(
            ticket_id=t["id"], text=t["text"],
            chosen_agent=chosen_name, expected=t["expected"],
            correct=(chosen_name == t["expected"]) if t["expected"] else (chosen_name is None),
            reply=reply, elapsed_ms=elapsed,
            coord_in_tokens=cin, coord_out_tokens=cout,
            exec_in_tokens=ein, exec_out_tokens=eout,
        ))
    return results
