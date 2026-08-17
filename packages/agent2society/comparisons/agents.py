"""Agent registry for benchmark comparisons."""
from __future__ import annotations

AGENT_REGISTRY = {
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
        },
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
    },
}


def execute_agent(agent_id: str, skill_id: str, task_text: str) -> tuple[str, int, int]:
    """Simulate agent execution and return (result_text, input_tokens, output_tokens).

    Token counts are deterministic mocks that scale realistically with task length.
    """
    words = len(task_text.split())
    input_tokens = int(words * 1.33) + 15
    output_tokens = words * 8 + 60
    result = (
        f"[{agent_id}::{skill_id}] Result for: {task_text[:80]}... "
        f"(mocked output with {output_tokens} tokens)"
    )
    return result, input_tokens, output_tokens
