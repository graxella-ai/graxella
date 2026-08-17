"""Extended AGENT_REGISTRY for v2 benchmarks.

Re-uses v1's four agents but adds:
  - `boundary` annotations declaring deny terms enforced by agent2society's
    conformance check (the baseline has no equivalent and will silently
    route prohibited tasks to the wrong agent).
  - `execute_agent` is re-exported so v2 modules don't import from v1.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

AGENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "research_agent": {
        "description": (
            "Specialist in internet research, competitive intelligence, "
            "academic literature, and structured data collection from "
            "primary and secondary sources."
        ),
        "skills": {
            "web_research": (
                "Search the open web, news feeds, and curated databases "
                "to surface relevant, recent, and authoritative information "
                "on any topic."
            ),
            "competitor_analysis": (
                "Identify, profile, and benchmark competitors across product "
                "features, pricing, go-to-market strategy, and market share."
            ),
            "literature_review": (
                "Systematically survey academic papers, whitepapers, and "
                "industry reports to synthesise existing knowledge on a topic."
            ),
            "data_collection": (
                "Gather structured datasets from APIs, scrapers, surveys, "
                "and public repositories; validate and normalise raw data."
            ),
        },
        # boundary used by agent2society conformance enforcement
        "boundary": {"allow": [], "deny": []},
    },
    "analysis_agent": {
        "description": (
            "Quantitative and qualitative analyst covering statistics, "
            "business trends, root-cause investigations, enterprise risk, "
            "and consumer/market sentiment."
        ),
        "skills": {
            "statistical_analysis": (
                "Apply descriptive statistics, hypothesis tests, regression, "
                "clustering, and time-series decomposition to numeric datasets."
            ),
            "trend_analysis": (
                "Identify directional patterns, inflection points, and "
                "seasonality in business, technology, or market data."
            ),
            "root_cause_analysis": (
                "Diagnose the underlying drivers of anomalies, outages, "
                "churn spikes, or performance degradation using structured "
                "methodologies (5-Whys, fishbone, fault-tree)."
            ),
            "risk_assessment": (
                "Evaluate operational, financial, regulatory, and strategic "
                "risks; score likelihood vs. impact; propose mitigations."
            ),
            "sentiment_analysis": (
                "Extract and quantify sentiment polarity and emotional tone "
                "from customer reviews, social media, news, and survey text."
            ),
            "compliance_review": (
                "Review controls, policies, and process evidence for "
                "regulatory frameworks (SOC2, GDPR, HIPAA, PCI); identify "
                "gaps and produce a conformance scoring matrix."
            ),
        },
        "boundary": {"allow": [], "deny": []},
    },
    "writer_agent": {
        "description": (
            "Professional business writer who produces polished executive "
            "communications, board materials, market reports, investor "
            "updates, and technical documentation."
        ),
        "skills": {
            "exec_memo": (
                "Draft concise executive memos that summarise a situation, "
                "state options, and recommend a clear course of action."
            ),
            "board_report": (
                "Compose formal board-level reports with an agenda, "
                "financials summary, key decisions required, and appendices."
            ),
            "market_report": (
                "Write comprehensive market research reports with executive "
                "summary, TAM/SAM/SOM sizing, competitive landscape, "
                "and strategic implications."
            ),
            "investor_update": (
                "Produce quarterly or milestone investor update letters covering "
                "KPIs, highlights, risks, and fundraising narrative."
            ),
            "technical_doc": (
                "Author API references, architecture decision records, runbooks, "
                "and system design documents for engineering audiences."
            ),
        },
        # writer is a narrator: it must never be handed raw, unprocessed
        # financial data (a frequent supervisor mis-route). Conformance
        # enforces this; baseline cannot.
        "boundary": {
            "allow": [],
            "deny": ["raw transactions ledger", "uncleaned ledger dump"],
        },
    },
    "data_agent": {
        "description": (
            "Data science specialist for market sizing, financial modelling, "
            "predictive forecasting, and visual data storytelling."
        ),
        "skills": {
            "market_sizing": (
                "Estimate total addressable, serviceable addressable, and "
                "serviceable obtainable market using top-down and bottom-up "
                "approaches with validated assumptions."
            ),
            "financial_modeling": (
                "Build three-statement financial models, DCFs, LBO models, "
                "unit economics frameworks, and scenario analyses in "
                "structured tabular form."
            ),
            "forecasting": (
                "Generate revenue, cost, headcount, and demand forecasts "
                "using statistical time-series models (ARIMA, Prophet) and "
                "machine-learning regression."
            ),
            "data_visualization": (
                "Design clear, accurate, and compelling charts, dashboards, "
                "and infographics that communicate quantitative insights to "
                "non-technical stakeholders."
            ),
        },
        "boundary": {"allow": [], "deny": []},
    },
}


def execute_agent(
    agent_id: str, skill_id: str, task_text: str
) -> Tuple[str, int, int]:
    """Simulate agent execution and return (result_text, input_tokens, output_tokens).

    Deterministic mock that scales with task length. No API calls.
    """
    words = len(task_text.split())
    input_tokens = int(words * 1.33) + 15
    output_tokens = words * 8 + 60
    result = (
        f"[{agent_id}::{skill_id}] Result for: {task_text[:80]}... "
        f"(mocked output with {output_tokens} tokens)"
    )
    return result, input_tokens, output_tokens


def agent_catalog_text() -> str:
    """Returns the full agent+skill catalog as a long prompt block.

    Used as the supervisor system prompt in baseline. ~350-450 tokens.
    """
    lines = [
        "You are an enterprise multi-agent orchestration supervisor.",
        "Below is the complete catalog of agents and their declared skills.",
        "For each incoming task you MUST: (a) identify the single most",
        "appropriate (agent, skill) pair, (b) think briefly about why",
        "alternatives are inferior, (c) emit a JSON routing decision.",
        "",
        "All agents implement the A2A JSON-RPC contract. Reply ONLY with",
        "valid JSON of the form:",
        '  {"agent": "<agent_id>", "skill": "<skill_id>", "reason": "<one line>"}',
        "",
        "AGENT CATALOG:",
    ]
    for agent_id, agent_info in AGENT_REGISTRY.items():
        lines.append("")
        lines.append(f"Agent: {agent_id}")
        lines.append(f"  Description: {agent_info['description']}")
        lines.append("  Skills:")
        for skill_id, skill_desc in agent_info["skills"].items():
            lines.append(f"    - {skill_id}: {skill_desc}")
        b = agent_info.get("boundary", {})
        if b.get("deny"):
            lines.append("  Deny terms: " + ", ".join(b["deny"]))
    lines.append("")
    lines.append("Choose the MOST appropriate pair. Reply with JSON only.")
    return "\n".join(lines)
