"""EscalationHandler agent -- A2A protocol-native.

Handles complex, repeated, or critical complaints that require senior
support, legal review, or out-of-policy compensation.
"""
from __future__ import annotations

import uuid

from a2a.helpers.proto_helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCard, AgentCapabilities, AgentInterface, AgentSkill


PORT = 8103
URL = f"http://127.0.0.1:{PORT}"


CARD = AgentCard(
    name="EscalationHandler",
    description=(
        "Triage escalated complaints requiring senior support attention, "
        "manage SLA for repeated failures, approve out-of-policy "
        "compensation, and assign cases to L2 agents."
    ),
    version="1.0.0",
    supported_interfaces=[AgentInterface(url=URL, protocol_binding="JSONRPC")],
    capabilities=AgentCapabilities(streaming=False),
    default_input_modes=["text"],
    default_output_modes=["text"],
    skills=[
        AgentSkill(
            id="escalate",
            name="Escalation Management",
            description=(
                "Open an escalation case for a complaint, assign senior "
                "agent, set SLA, approve compensation. Handles repeated "
                "failures, critical complaints, and out-of-policy requests."
            ),
            tags=[
                "escalate", "escalation", "complaint", "critical", "senior",
                "L2", "SLA", "compensation", "legal", "repeated", "compensate",
            ],
        )
    ],
)


class Executor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        case = f"ESC-{uuid.uuid4().hex[:8].upper()}"
        priority = "P1" if any(w in text.lower() for w in ("again", "third", "unacceptable")) else "P2"
        reply = new_text_message(
            text=(
                f"Escalation opened: case={case} priority={priority} "
                f"assignee=L2.queue SLA=4h"
            )
        )
        await event_queue.enqueue_event(reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return
