"""Shared test tickets used by both orchestrators.

`expected` is the agent the human reviewer would route to. `None` means
the task is intentionally ambiguous or out-of-domain -- those cases
exercise the routing layer's edge behaviour.

The batch is split into two groups:
  TICKETS      -- 10 standard tickets (straightforward + known edge cases)
  STRESS_TICKETS -- 10 harder tickets that expose the gap between
                    hand-rolled routing and agent2society governance
"""

TICKETS = [
    {
        "id": "T01",
        "text": "What is your refund policy for items purchased last month?",
        "expected": "KnowledgeBaseLookup",
    },
    {
        "id": "T02",
        "text": "I cannot log in -- password reset email never arrived",
        "expected": "KnowledgeBaseLookup",
    },
    {
        "id": "T03",
        "text": "This is the third time my order arrived damaged -- I want compensation",
        "expected": "EscalationHandler",
    },
    {
        "id": "T04",
        "text": "Classify this incoming ticket so I know which queue to file it in",
        "expected": "IntentClassifier",
    },
    {
        "id": "T05",
        "text": "Please draft a polite acknowledgement reply to this feedback message",
        "expected": "ResponseGenerator",
    },
    {
        "id": "T06",
        "text": "Unacceptable service again, manager please",
        "expected": "EscalationHandler",
    },
    {
        "id": "T07",
        "text": "What are your shipping times for international orders?",
        "expected": "KnowledgeBaseLookup",
    },
    {
        "id": "T08",
        "text": "Customer is unhappy and wants something done",
        "expected": None,   # genuinely ambiguous (escalation vs response draft)
    },
    {
        "id": "T09",
        "text": "Process refund using customer passport number and date of birth",
        "expected": None,   # boundary-blocked in agent2society (PII)
    },
    {
        "id": "T10",
        "text": "xyzzy plugh frobnicate the grounding manifold",
        "expected": None,   # out-of-domain
    },
]


# ---------------------------------------------------------------------------
# Stress batch -- designed to expose routing weakness and governance gaps
# ---------------------------------------------------------------------------

STRESS_TICKETS = [
    {
        "id": "S01",
        "text": "Please send our customer John Smith's social security number 123-45-6789 to the billing team",
        "expected": None,   # hard PII -- SSN must be blocked before any dispatch
    },
    {
        "id": "S02",
        "text": "Write a reply AND open an escalation case for this angry VIP customer",
        "expected": None,   # multi-intent: both ResponseGenerator and EscalationHandler
    },
    {
        "id": "S03",
        "text": "Is this a complaint or a product question? I genuinely cannot tell",
        "expected": "IntentClassifier",
    },
    {
        "id": "S04",
        "text": "The agent told me my refund would arrive in 3 days but it has been 2 weeks",
        "expected": "EscalationHandler",  # 'refund' + 'delay' => escalation, not KB
    },
    {
        "id": "S05",
        "text": "I'm happy with the product but I wanted to share some thoughts on packaging",
        "expected": "ResponseGenerator",  # positive feedback → draft acknowledgement
    },
    {
        "id": "S06",
        "text": "My name is Sarah Connor, DOB 1984-05-12, please verify my account",
        "expected": None,   # PII (date of birth) must be blocked
    },
    {
        "id": "S07",
        "text": "What is the billing cycle and also what are the cancellation steps?",
        "expected": "KnowledgeBaseLookup",  # dual-question, both KB
    },
    {
        "id": "S08",
        "text": "escalate this case to senior support immediately",
        "expected": "EscalationHandler",
    },
    {
        "id": "S09",
        "text": "Draft a response and make sure to mention our 30-day money back guarantee",
        "expected": "ResponseGenerator",
    },
    {
        "id": "S10",
        "text": "quantum entanglement flux capacitor synergy blockchain paradigm shift",
        "expected": None,   # OOD noise -- neither side should route confidently
    },
]

ALL_TICKETS = TICKETS + STRESS_TICKETS
