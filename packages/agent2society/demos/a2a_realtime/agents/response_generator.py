"""ResponseGenerator agent -- A2A protocol-native.

Drafts a customer-facing reply text for general queries, feedback
acknowledgements, and follow-ups. Used when no specific KB hit or
escalation criteria match.
"""
from __future__ import annotations

from a2a.helpers.proto_helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCard, AgentCapabilities, AgentInterface, AgentSkill


PORT = 8104
URL = f"http://127.0.0.1:{PORT}"


CARD = AgentCard(
    name="ResponseGenerator",
    description=(
        "Draft a polished customer-facing reply for general queries, "
        "feedback acknowledgements, and follow-up messages where no "
        "specific knowledge-base answer or escalation rule applies."
    ),
    version="1.0.0",
    supported_interfaces=[AgentInterface(url=URL, protocol_binding="JSONRPC")],
    capabilities=AgentCapabilities(streaming=False),
    default_input_modes=["text"],
    default_output_modes=["text"],
    skills=[
        AgentSkill(
            id="draft_reply",
            name="Draft Customer Reply",
            description=(
                "Compose a polite, polished reply message for the customer. "
                "Used for feedback acknowledgements, general thanks, "
                "follow-up notes, and conversational replies that do not "
                "fit billing, knowledge-base, or escalation paths."
            ),
            tags=[
                "draft", "reply", "compose", "write", "acknowledge",
                "feedback", "thanks", "polish", "tone", "customer-facing",
            ],
        )
    ],
)


class Executor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        snippet = text[:60] + ("..." if len(text) > 60 else "")
        reply = new_text_message(
            text=(
                f'Drafted reply: "Thanks for reaching out. We have received '
                f'your message regarding: {snippet}  We will follow up shortly."'
            )
        )
        await event_queue.enqueue_event(reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return
