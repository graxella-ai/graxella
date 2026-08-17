"""Complex multi-step scenarios for the agent2society vs. LangGraph benchmark.

A `Scenario` is a workflow that requires several distinct agent skills in
sequence (with optional branching). It's the realistic case multi-agent
meshes are sold for — and the case where a supervisor LLM pays the most
coordination tokens because every step incurs another LLM round-trip
with a growing prompt.

Each Scenario carries:
  * `objective` — the high-level task as a user would phrase it
  * `expected_steps` — ground-truth list of (agent, sub_task) pairs

Both methods are given the same `objective` and `expected_steps`:
  * LangGraph supervisor: receives `objective` plus the agent roster,
    pays one supervisor LLM call per routing decision (plus one to
    decide FINISH).
  * agent2society: receives the pre-decomposed `expected_steps` from the
    user — v1 agent2society is single-shot routing, not a planner. The
    benchmark is transparent about this tradeoff.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class Scenario:
    name: str
    objective: str
    expected_steps: List[Tuple[str, str]]  # (agent_name, sub_task)

    @property
    def step_count(self) -> int:
        return len(self.expected_steps)


def default_scenarios() -> List[Scenario]:
    """Three complex scenarios spanning 6 agents.

    Sized so each exercises a different mesh shape:
      * `quarterly_brief`     — 4 steps, linear pipeline
      * `legal_intake`        — 3 steps, requires conformance to bite
      * `enterprise_launch`   — 5 steps, longest chain
    """
    return [
        Scenario(
            name="quarterly_brief",
            objective=(
                "Produce a quarterly customer-success brief for the exec team. "
                "Research Q3 churn drivers, analyse correlations between churn "
                "and customer segments, draft an exec memo of the findings, "
                "then translate the memo into Spanish for the LATAM org."
            ),
            expected_steps=[
                (
                    "research-agent",
                    "Research Q3 customer churn drivers and gather sources",
                ),
                (
                    "analyst-agent",
                    "Compute correlations between churn and customer segments from the customer feature table",
                ),
                (
                    "writer-agent",
                    "Draft an executive memo summarising the churn findings",
                ),
                (
                    "translator-agent",
                    "Translate the executive memo into Spanish",
                ),
            ],
        ),
        Scenario(
            name="legal_intake",
            objective=(
                "Vendor sent us a draft NDA. Review the contract, flag risky "
                "clauses, then draft a one-paragraph exec memo on the top "
                "risks."
            ),
            expected_steps=[
                (
                    "legal-agent",
                    "Review the vendor NDA and flag risky clauses and indemnification gaps",
                ),
                (
                    "writer-agent",
                    "Draft a one-paragraph executive memo summarising the top contract risks",
                ),
            ],
        ),
        Scenario(
            name="enterprise_launch",
            objective=(
                "Prep the enterprise launch announcement. Research competitor "
                "messaging, analyse our retention numbers from the analyst "
                "data, draft the announcement memo, translate the memo to "
                "French, and finally write the Python snippet that posts the "
                "release notification to the internal API."
            ),
            expected_steps=[
                (
                    "research-agent",
                    "Research competitor messaging for enterprise tier launches",
                ),
                (
                    "analyst-agent",
                    "Analyse retention numbers and produce a chart of cohort retention",
                ),
                (
                    "writer-agent",
                    "Draft the enterprise launch announcement executive memo",
                ),
                (
                    "translator-agent",
                    "Translate the announcement memo into French",
                ),
                (
                    "coder-agent",
                    "Write a Python function that posts the release notification to the internal API",
                ),
            ],
        ),
    ]
