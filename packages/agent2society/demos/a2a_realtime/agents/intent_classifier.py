"""IntentClassifier agent -- A2A protocol-native.

Classifies an incoming customer support ticket into one of:
  - billing
  - technical
  - feedback
  - escalation
"""
from __future__ import annotations

from a2a.helpers.proto_helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCard, AgentCapabilities, AgentInterface, AgentSkill


PORT = 8101
URL = f"http://127.0.0.1:{PORT}"


CARD = AgentCard(
    name="IntentClassifier",
    description=(
        "Classify customer support tickets into intent categories. "
        "Recognises billing questions, technical issues, product feedback, "
        "and complaints requiring escalation."
    ),
    version="1.0.0",
    supported_interfaces=[AgentInterface(url=URL, protocol_binding="JSONRPC")],
    capabilities=AgentCapabilities(streaming=False),
    default_input_modes=["text"],
    default_output_modes=["text"],
    skills=[
        AgentSkill(
            id="classify_intent",
            name="Classify Intent",
            description=(
                "Detect intent and category of an incoming customer support "
                "ticket message. Returns a label such as billing, technical, "
                "feedback, or escalation."
            ),
            tags=[
                "intent", "classify", "triage", "category", "label",
                "ticket", "incoming", "routing",
            ],
        )
    ],
)


_RULES = [
    ("billing", ("invoice", "charge", "billing", "refund", "payment", "card", "subscription")),
    ("technical", ("error", "crash", "bug", "login", "password", "reset", "broken", "fails")),
    ("escalation", ("complaint", "again", "third time", "manager", "unacceptable", "compensation")),
    ("feedback", ("feedback", "suggestion", "review", "love", "hate", "wish")),
]


def _classify(text: str) -> str:
    t = text.lower()
    best = ("general", 0)
    for label, kws in _RULES:
        hits = sum(1 for kw in kws if kw in t)
        if hits > best[1]:
            best = (label, hits)
    return best[0]


class Executor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        label = _classify(text)
        reply = new_text_message(
            text=f"intent={label}; confidence={'high' if label != 'general' else 'low'}"
        )
        await event_queue.enqueue_event(reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return
