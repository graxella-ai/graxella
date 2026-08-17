"""KnowledgeBaseLookup agent -- A2A protocol-native.

Searches the FAQ / KB for a canned answer to common how-to and policy
questions (refunds, password reset, shipping, billing cycle).
"""
from __future__ import annotations

from a2a.helpers.proto_helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCard, AgentCapabilities, AgentInterface, AgentSkill


PORT = 8102
URL = f"http://127.0.0.1:{PORT}"


CARD = AgentCard(
    name="KnowledgeBaseLookup",
    description=(
        "Search the customer support knowledge base and FAQ for canned "
        "answers to standard how-to and policy questions about refunds, "
        "password reset, shipping windows, billing cycles, and account "
        "management."
    ),
    version="1.0.0",
    supported_interfaces=[AgentInterface(url=URL, protocol_binding="JSONRPC")],
    capabilities=AgentCapabilities(streaming=False),
    default_input_modes=["text"],
    default_output_modes=["text"],
    skills=[
        AgentSkill(
            id="kb_search",
            name="Knowledge Base Search",
            description=(
                "Look up answers in the FAQ knowledge base for standard "
                "questions about refund policy, password reset procedure, "
                "shipping times, billing cycle, and account settings."
            ),
            tags=[
                "FAQ", "knowledge", "lookup", "policy", "refund", "password",
                "shipping", "billing", "account", "how-to", "answer",
            ],
        )
    ],
)


_KB = [
    (("refund", "policy"),
     "Refund policy: items can be returned within 30 days of purchase with original receipt for full refund."),
    (("password", "reset"),
     "Password reset: visit /account/reset, enter your email; a reset link is valid for 1 hour."),
    (("shipping", "delivery"),
     "Standard shipping: 3-5 business days domestic, 7-14 international. Express: 1-2 days."),
    (("billing", "cycle"),
     "Billing cycle: monthly subscriptions renew on the same calendar day each month."),
    (("invoice", "download"),
     "Invoices: download from /account/billing under 'Receipts'. PDF format."),
]


def _lookup(text: str) -> str | None:
    t = text.lower()
    for kws, ans in _KB:
        if all(kw in t for kw in kws[:1]):
            # First keyword must hit; additional keywords boost confidence
            if any(kw in t for kw in kws):
                return ans
    return None


class Executor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        ans = _lookup(text)
        if ans:
            reply = new_text_message(text=f"KB answer: {ans}")
        else:
            reply = new_text_message(text="KB miss: no FAQ entry matched.")
        await event_queue.enqueue_event(reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return
