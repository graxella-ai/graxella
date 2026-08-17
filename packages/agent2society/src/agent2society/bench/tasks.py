"""Labeled task suite for the benchmark.

Each TaskSpec carries the task text plus the (agent, skill) ground truth.
The harness uses ground truth to score task success — held equal between
agent2society and the supervisor baseline so the headline number is honestly
about coordination cost.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TaskSpec:
    task: str
    expected_agent: str
    expected_skill: str
    tags: List[str] = field(default_factory=list)


def default_suite() -> List[TaskSpec]:
    """A small mixed suite that exercises mesh-typical routing decisions."""
    return [
        TaskSpec(
            task="Research Q3 customer churn drivers from the web",
            expected_agent="research-agent",
            expected_skill="web_research",
            tags=["research"],
        ),
        TaskSpec(
            task="Find recent EU fintech regulations and summarise the changes",
            expected_agent="research-agent",
            expected_skill="web_research",
            tags=["research"],
        ),
        TaskSpec(
            task="Draft an executive memo on customer churn for the board",
            expected_agent="writer-agent",
            expected_skill="exec_memo",
            tags=["writing"],
        ),
        TaskSpec(
            task="Turn these notes into a one-page exec memo on Q3 retention",
            expected_agent="writer-agent",
            expected_skill="exec_memo",
            tags=["writing"],
        ),
        TaskSpec(
            task="Write a Python function that dedupes a list of dicts by key",
            expected_agent="coder-agent",
            expected_skill="python_code",
            tags=["code"],
        ),
        TaskSpec(
            task="Refactor this Python module to remove duplicated logic",
            expected_agent="coder-agent",
            expected_skill="python_code",
            tags=["code"],
        ),
        TaskSpec(
            task="Analyse last quarter's sales figures and chart top regions",
            expected_agent="analyst-agent",
            expected_skill="data_analysis",
            tags=["analysis"],
        ),
        TaskSpec(
            task="Compute correlations across the customer feature table",
            expected_agent="analyst-agent",
            expected_skill="data_analysis",
            tags=["analysis"],
        ),
        TaskSpec(
            task="Translate this product brief from English to Spanish",
            expected_agent="translator-agent",
            expected_skill="translate",
            tags=["translation"],
        ),
        TaskSpec(
            task="Localise this support article into French",
            expected_agent="translator-agent",
            expected_skill="translate",
            tags=["translation"],
        ),
        TaskSpec(
            task="Review this contract draft and flag risky clauses",
            expected_agent="legal-agent",
            expected_skill="contract_review",
            tags=["legal"],
        ),
        TaskSpec(
            task="Identify indemnification gaps in this NDA",
            expected_agent="legal-agent",
            expected_skill="contract_review",
            tags=["legal"],
        ),
    ]


def default_mesh_cards() -> List[dict]:
    """The mesh the default suite is labeled against.

    Six agents on (notionally) different frameworks. Cards are A2A-shaped
    so they can be loaded straight into a Mesh.
    """
    return [
        {
            "name": "research-agent",
            "description": "Researches topics on the web; returns sourced summaries.",
            "url": "local://research-agent",
            "skills": [
                {
                    "id": "web_research",
                    "name": "Web Research",
                    "description": "Search the web, gather sources, summarise findings on a given topic.",
                    "tags": ["research", "search", "web", "sources", "summary"],
                }
            ],
        },
        {
            "name": "writer-agent",
            "description": "Drafts executive memos and short business writing from notes.",
            "url": "local://writer-agent",
            "skills": [
                {
                    "id": "exec_memo",
                    "name": "Executive Memo",
                    "description": "Draft an executive memo from notes or bullet points.",
                    "tags": ["writing", "memo", "exec", "draft", "business"],
                }
            ],
        },
        {
            "name": "coder-agent",
            "description": "Writes, refactors, and debugs Python code.",
            "url": "local://coder-agent",
            "skills": [
                {
                    "id": "python_code",
                    "name": "Write Python",
                    "description": "Write Python functions, refactor, debug code.",
                    "tags": ["code", "python", "programming", "refactor"],
                }
            ],
        },
        {
            "name": "analyst-agent",
            "description": "Analyses tabular and statistical data; produces summaries and charts.",
            "url": "local://analyst-agent",
            "skills": [
                {
                    "id": "data_analysis",
                    "name": "Data Analysis",
                    "description": "Statistical analysis, correlations, sales figures, charts.",
                    "tags": ["analysis", "data", "statistics", "sales", "chart"],
                }
            ],
        },
        {
            "name": "translator-agent",
            "description": "Translates and localises text between languages.",
            "url": "local://translator-agent",
            "skills": [
                {
                    "id": "translate",
                    "name": "Translate",
                    "description": "Translate or localise text from one language to another.",
                    "tags": ["translation", "translate", "localise", "language"],
                }
            ],
        },
        {
            "name": "legal-agent",
            "description": "Reviews contracts and legal documents; flags risk.",
            "url": "local://legal-agent",
            "skills": [
                {
                    "id": "contract_review",
                    "name": "Contract Review",
                    "description": "Review contracts, NDAs, identify risky clauses and indemnification gaps.",
                    "tags": ["legal", "contract", "review", "risk", "nda"],
                }
            ],
        },
    ]
