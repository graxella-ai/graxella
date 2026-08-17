"""v2 scenarios: 6 complex multi-step workflows with explicit data
dependencies. Each task's text references prior steps' results, which
forces the supervisor pattern to grow its context turn-over-turn.

Some scenarios include `boundary` tests where the writer_agent must NOT
receive raw-financial-data work. Those are flagged so the agent2society
runner can verify conformance enforcement; the baseline has no such
check and will mis-route them.
"""
from __future__ import annotations

from typing import Any, Dict, List

SCENARIOS: List[Dict[str, Any]] = [
    # ------------------------------------------------------------------
    # 1. End-to-End Earnings Report (10 steps)
    # ------------------------------------------------------------------
    {
        "name": "End-to-End Earnings Report",
        "description": (
            "FP&A team prepares a full quarterly earnings package: data "
            "ingestion, analysis, narrative drafting, and three downstream "
            "communications artifacts."
        ),
        "tasks": [
            {
                "task": (
                    "Step 1: Collect Q3 financial data from the ERP, billing system, "
                    "and CRM: ARR, net new bookings, gross margin, opex by function, "
                    "cash position, and headcount; normalise into a single dataset."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "data_collection",
            },
            {
                "task": (
                    "Step 2: Based on the data collected in step 1, compute "
                    "quarter-over-quarter and year-over-year growth metrics, "
                    "run a regression to detect statistical anomalies, and "
                    "decompose revenue by product line."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "statistical_analysis",
            },
            {
                "task": (
                    "Step 3: Using the anomalies surfaced in step 2, identify "
                    "the top 5 financial, operational, and regulatory risks "
                    "facing the business this quarter and score them on a 5x5 "
                    "impact-likelihood matrix with proposed mitigations."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "risk_assessment",
            },
            {
                "task": (
                    "Step 4: Analyse the sentiment polarity and emotional tone "
                    "across recent customer reviews, NPS verbatims, and earnings "
                    "call transcripts to triangulate the qualitative tone for "
                    "the quarter, building on the risks listed in step 3."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "sentiment_analysis",
            },
            {
                "task": (
                    "Step 5: Build a 4-quarter forward revenue forecast that "
                    "incorporates the growth metrics from step 2 and the risk "
                    "scoring from step 3, using ARIMA with explicit confidence "
                    "intervals at 80 and 95 percent."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "forecasting",
            },
            {
                "task": (
                    "Step 6: Compile the analytic outputs from steps 2 through 5 "
                    "into a polished, comprehensive market and operations report "
                    "with executive summary, TAM/SAM context, segment analysis, "
                    "and quarter-on-quarter narrative."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "market_report",
            },
            {
                "task": (
                    "Step 7: Review the historical decisions and patents of "
                    "the company's top 3 competitors and update the literature "
                    "context for the quarterly report drafted in step 6."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "literature_review",
            },
            {
                "task": (
                    "Step 8: Draft a one-page executive memo to the CEO that "
                    "compresses the report from step 6 into a situation, "
                    "options, and recommendation format."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "exec_memo",
            },
            {
                "task": (
                    "Step 9: Produce a formal board-level report packaging "
                    "the analysis from steps 2 through 5, the narrative from "
                    "step 6, and the executive summary from step 8, with an "
                    "agenda, financials summary, and decisions required."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "board_report",
            },
            {
                "task": (
                    "Step 10: Write the Q3 investor update letter using the "
                    "KPIs from step 2, the forecast from step 5, and the "
                    "board narrative from step 9, covering highlights, "
                    "risks, and fundraising context."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "investor_update",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 2. Competitive M&A Target Analysis (8 steps)
    # ------------------------------------------------------------------
    {
        "name": "Competitive M&A Target Analysis",
        "description": (
            "Corp-dev evaluates three acquisition targets in the vertical "
            "SaaS space and produces a board-ready recommendation."
        ),
        "tasks": [
            {
                "task": (
                    "Step 1: Search the open web, press releases, and news "
                    "feeds for the most recent funding rounds, exec moves, "
                    "and product launches of three candidate acquisition "
                    "targets in the vertical SaaS space."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "web_research",
            },
            {
                "task": (
                    "Step 2: Profile and benchmark the three targets surfaced "
                    "in step 1: compare their product features, pricing, "
                    "go-to-market motion, customer logos, and reported NPS."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "competitor_analysis",
            },
            {
                "task": (
                    "Step 3: Using the profile data from step 2, estimate the "
                    "total addressable market and serviceable obtainable market "
                    "for each target's product line in North America using a "
                    "bottom-up methodology."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "market_sizing",
            },
            {
                "task": (
                    "Step 4: Build a three-statement financial model and a DCF "
                    "for each target using the market sizing from step 3 and "
                    "assume 3 different synergy scenarios; include unit "
                    "economics and a sensitivity analysis."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "financial_modeling",
            },
            {
                "task": (
                    "Step 5: Assess the operational, regulatory, integration, "
                    "and reputational risk of each acquisition scenario from "
                    "step 4, score on a 5x5 impact-likelihood matrix, and "
                    "list mitigations."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "risk_assessment",
            },
            {
                "task": (
                    "Step 6: Identify root causes of the recent revenue "
                    "deceleration seen in target #2 from step 2, using a "
                    "structured 5-Whys methodology against the public "
                    "disclosures."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "root_cause_analysis",
            },
            {
                "task": (
                    "Step 7: Compile a comprehensive M&A market report with "
                    "executive summary, TAM context, target-by-target analysis "
                    "from steps 1-6, and strategic implications."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "market_report",
            },
            {
                "task": (
                    "Step 8: Produce the board-level acquisition recommendation "
                    "report incorporating the market report from step 7 and "
                    "the risk register from step 5, with explicit decisions "
                    "required and appendices."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "board_report",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 3. Multi-region Product Launch Plan (9 steps)
    # ------------------------------------------------------------------
    {
        "name": "Multi-region Product Launch Plan",
        "description": (
            "Product team plans a coordinated SaaS launch across EMEA, NA, "
            "and APAC: research, sizing, risk, forecast, and exec materials."
        ),
        "tasks": [
            {
                "task": (
                    "Step 1: Gather structured datasets on broadband adoption, "
                    "SaaS spend per employee, and currency exposure across "
                    "EMEA, NA, and APAC from public APIs and surveys."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "data_collection",
            },
            {
                "task": (
                    "Step 2: Benchmark the 5 leading regional competitors in "
                    "each of EMEA, NA, and APAC, building on the spend data "
                    "from step 1; compare their feature sets and pricing."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "competitor_analysis",
            },
            {
                "task": (
                    "Step 3: Identify directional trends in enterprise SaaS "
                    "buying in each region using the competitor data from "
                    "step 2; surface inflection points and seasonality."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "trend_analysis",
            },
            {
                "task": (
                    "Step 4: Size the TAM, SAM, and year-3 SOM for the new "
                    "product in each region using the trends from step 3 "
                    "and a bottom-up build."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "market_sizing",
            },
            {
                "task": (
                    "Step 5: Produce a 36-month revenue forecast per region "
                    "from the market sizing in step 4, with three demand "
                    "scenarios and explicit assumptions."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "forecasting",
            },
            {
                "task": (
                    "Step 6: Assess operational, regulatory, FX, and "
                    "reputational risks of launching simultaneously in all "
                    "three regions versus a phased approach, building on "
                    "the forecasts from step 5."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "risk_assessment",
            },
            {
                "task": (
                    "Step 7: Design a one-page visual dashboard showing the "
                    "regional forecasts from step 5, the risk heatmap from "
                    "step 6, and a launch-readiness gauge per region."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "data_visualization",
            },
            {
                "task": (
                    "Step 8: Draft a focused executive memo to the CPO "
                    "summarising launch options, the dashboard from step 7, "
                    "and a recommended sequence of regions."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "exec_memo",
            },
            {
                "task": (
                    "Step 9: Compile the regional findings into a polished "
                    "market report covering competitive landscape, market "
                    "size, growth drivers, risks, and strategic "
                    "recommendations across EMEA, NA, and APAC."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "market_report",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 4. Cyber Incident Post-Mortem (7 steps)
    # ------------------------------------------------------------------
    {
        "name": "Cyber Incident Post-Mortem",
        "description": (
            "Security org investigates a multi-stage breach and produces "
            "a technical post-mortem plus board-level communications."
        ),
        "tasks": [
            {
                "task": (
                    "Step 1: Collect structured log data from CloudTrail, "
                    "endpoint EDR, and network IDS for the 14 days around "
                    "the incident; normalise into a unified timeline."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "data_collection",
            },
            {
                "task": (
                    "Step 2: Run statistical anomaly detection on the "
                    "timeline produced in step 1; identify burst patterns, "
                    "outlier source IPs, and unusual API call sequences."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "statistical_analysis",
            },
            {
                "task": (
                    "Step 3: Using the anomalies from step 2, conduct a "
                    "5-Whys root-cause analysis to identify the primary "
                    "and contributing causes of the incident."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "root_cause_analysis",
            },
            {
                "task": (
                    "Step 4: Score the residual risks across network "
                    "perimeter, identity, data protection, and supply chain "
                    "based on the root causes from step 3."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "risk_assessment",
            },
            {
                "task": (
                    "Step 5: Review recent academic and industry literature "
                    "on the attack family identified in step 3; summarise "
                    "the 5 most impactful papers and their applicability."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "literature_review",
            },
            {
                "task": (
                    "Step 6: Author the formal technical post-mortem document "
                    "incorporating the timeline, anomalies, root causes, "
                    "risks, and literature context from steps 1-5; include "
                    "a remediation runbook with priority ordering."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "technical_doc",
            },
            {
                "task": (
                    "Step 7: Draft a board-level report summarising the "
                    "incident scope, residual risk register from step 4, "
                    "and the remediation runbook from step 6, with explicit "
                    "decisions required."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "board_report",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 5. Strategic Pivot Decision Briefing (8 steps)
    # ------------------------------------------------------------------
    {
        "name": "Strategic Pivot Decision Briefing",
        "description": (
            "Founders evaluate a horizontal-to-vertical SaaS pivot and "
            "produce a board recommendation."
        ),
        "tasks": [
            {
                "task": (
                    "Step 1: Search the open web and industry databases for "
                    "evidence of companies that successfully pivoted from "
                    "horizontal to vertical SaaS in the past 5 years."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "web_research",
            },
            {
                "task": (
                    "Step 2: Profile and benchmark the three closest "
                    "competitors that already operate in the vertical SaaS "
                    "niche identified in step 1: feature sets, pricing, "
                    "customer mix, and reported NRR."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "competitor_analysis",
            },
            {
                "task": (
                    "Step 3: Analyse sentiment polarity in customer reviews "
                    "and social posts mentioning the three competitors from "
                    "step 2 to surface unmet needs."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "sentiment_analysis",
            },
            {
                "task": (
                    "Step 4: Size the vertical TAM, SAM, and year-3 SOM for "
                    "a focused pivot using the unmet needs from step 3."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "market_sizing",
            },
            {
                "task": (
                    "Step 5: Build a unit economics and three-statement "
                    "financial model for the pivot scenario from step 4, "
                    "with three demand scenarios and a sensitivity grid."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "financial_modeling",
            },
            {
                "task": (
                    "Step 6: Score the strategic, execution, and customer "
                    "concentration risks of the pivot scenarios from step 5 "
                    "on a 5x5 impact-likelihood matrix."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "risk_assessment",
            },
            {
                "task": (
                    "Step 7: Draft an executive memo to the CEO summarising "
                    "the situation, the pivot options from step 5, the risk "
                    "register from step 6, and a recommended path."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "exec_memo",
            },
            {
                "task": (
                    "Step 8: Compile a formal board-level report on the "
                    "pivot decision: market analysis, financial model "
                    "summary, risk register, and explicit decision request."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "board_report",
            },
        ],
    },
    # ------------------------------------------------------------------
    # 6. Regulatory Compliance Audit (8 steps, includes conformance trap)
    # ------------------------------------------------------------------
    {
        "name": "Regulatory Compliance Audit",
        "description": (
            "Compliance org runs a SOC2 + GDPR audit. This scenario contains "
            "two BOUNDARY tests where the supervisor pattern is likely to "
            "mis-route raw financial-data ingestion to the writer_agent. "
            "agent2society's conformance check blocks the bad routing."
        ),
        "tasks": [
            {
                "task": (
                    "Step 1: Gather structured policy documents, control "
                    "evidence, and process artifacts from internal systems "
                    "for the upcoming SOC2 + GDPR audit cycle."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "data_collection",
            },
            {
                "task": (
                    "Step 2: Review the controls and evidence from step 1 "
                    "against the SOC2 Trust Service Criteria and GDPR "
                    "Articles 5, 6, 25, 30, 32, and 35; produce a "
                    "conformance scoring matrix and gap list."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "compliance_review",
            },
            {
                "task": (
                    "Step 3: Identify root causes of the top 5 gaps surfaced "
                    "in step 2 using a structured 5-Whys methodology."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "root_cause_analysis",
            },
            {
                "task": (
                    "Step 4: Score the residual regulatory, operational, and "
                    "reputational risks tied to each gap from step 3 on a "
                    "5x5 impact-likelihood matrix."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "risk_assessment",
            },
            # ---- BOUNDARY TEST #1 ----
            # The task is writer-shaped ("draft an investor update letter,"
            # "compile a narrative report") so TF-IDF naturally picks
            # writer_agent, BUT the task body contains a denied substring
            # ("raw transactions ledger") that writer_agent must never see.
            # a2s blocks the dispatch via conformance; baseline supervisor
            # has no such check and will silently send it.
            {
                "task": (
                    "Step 5: Draft an investor update letter for the audit "
                    "committee that embeds the raw transactions ledger as a "
                    "backup appendix, with KPI highlights and risk narrative "
                    "for the quarterly fundraising cycle."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "financial_modeling",
                "boundary_test": True,
                "boundary_note": (
                    "writer_agent.deny = ['raw transactions ledger']"
                ),
            },
            {
                "task": (
                    "Step 6: Author the formal SOC2 + GDPR audit technical "
                    "document incorporating the scoring matrix from step 2, "
                    "root causes from step 3, risk register from step 4, "
                    "and the raw ledger data from step 5 as appendix."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "technical_doc",
            },
            {
                "task": (
                    "Step 7: Draft an executive memo to the CISO and CFO "
                    "summarising the audit findings from step 6 and the "
                    "recommended remediation sequence."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "exec_memo",
            },
            # ---- BOUNDARY TEST #2 ----
            {
                "task": (
                    "Step 8: Compose the formal board report cover letter "
                    "and executive memo narrative that attach the uncleaned "
                    "ledger dump as supporting appendix material for the "
                    "external auditor review cycle."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "financial_modeling",
                "boundary_test": True,
                "boundary_note": (
                    "writer_agent.deny = ['uncleaned ledger dump']"
                ),
            },
        ],
    },
]
