"""Benchmark scenarios for agent routing comparisons."""
from __future__ import annotations

SCENARIOS = [
    {
        "name": "Market Intelligence Report",
        "description": (
            "A strategy team needs a comprehensive view of a fast-moving "
            "SaaS market: who the players are, what the data says, and how "
            "to communicate findings to senior leadership."
        ),
        "tasks": [
            {
                "task": (
                    "Search the web for the top 10 cloud-based CRM vendors by revenue "
                    "in 2025, including funding rounds and headcount data."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "web_research",
            },
            {
                "task": (
                    "Profile Salesforce, HubSpot, and Pipedrive: compare their pricing "
                    "tiers, feature sets, integration ecosystems, and customer NPS scores."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "competitor_analysis",
            },
            {
                "task": (
                    "Compute the 3-year CAGR of the global CRM market using the dataset "
                    "collected, decompose by region (NA, EMEA, APAC), and run a "
                    "regression to project 2027 revenue."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "statistical_analysis",
            },
            {
                "task": (
                    "Estimate the total addressable market for AI-augmented CRM tools "
                    "using a bottom-up methodology: SME segment (< 500 employees) vs "
                    "enterprise (> 500 employees) in North America."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "market_sizing",
            },
            {
                "task": (
                    "Write a polished 12-page market intelligence report for the VP of "
                    "Strategy, covering executive summary, competitive landscape, "
                    "market size, growth drivers, and 3 strategic recommendations."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "market_report",
            },
        ],
    },
    {
        "name": "Customer Churn Deep-Dive",
        "description": (
            "A SaaS product team is seeing monthly churn spike from 2.1% to 4.8% "
            "and needs to understand root causes and model the financial impact."
        ),
        "tasks": [
            {
                "task": (
                    "Pull churn data from the past 18 months, segment by cohort "
                    "entry month, plan tier, and industry vertical, then produce "
                    "a survival curve for each segment."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "statistical_analysis",
            },
            {
                "task": (
                    "Identify the primary root causes of the churn spike that began "
                    "in Q3 2024: analyse support ticket themes, NPS verbatims, and "
                    "feature-usage drop-off signals using a structured 5-Whys approach."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "root_cause_analysis",
            },
            {
                "task": (
                    "Scrape and analyse customer reviews on G2, Capterra, and Trustpilot "
                    "from the last 6 months; extract the top 5 negative themes and "
                    "calculate sentiment polarity score per theme."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "sentiment_analysis",
            },
            {
                "task": (
                    "Build a 24-month financial model showing the P&L impact of churn "
                    "at 2%, 3%, 4%, and 5% monthly rates; include CAC payback period "
                    "and LTV:CAC ratio for each scenario."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "financial_modeling",
            },
            {
                "task": (
                    "Draft an executive memo to the CEO summarising the churn analysis: "
                    "what's driving it, the financial exposure in three scenarios, "
                    "and three prioritised action items."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "exec_memo",
            },
        ],
    },
    {
        "name": "Competitor AI Strategy",
        "description": (
            "The product team wants to benchmark its AI roadmap against three "
            "well-funded competitors before the next board meeting."
        ),
        "tasks": [
            {
                "task": (
                    "Conduct a deep-dive competitor analysis of OpenAI, Anthropic, and "
                    "Cohere: product portfolios, enterprise pricing, partnership networks, "
                    "hiring patterns, and recently filed patents."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "competitor_analysis",
            },
            {
                "task": (
                    "Review the academic literature on retrieval-augmented generation "
                    "published in 2024-2025; summarise the 5 most impactful papers "
                    "and their enterprise applicability."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "literature_review",
            },
            {
                "task": (
                    "Identify directional trends in enterprise AI procurement: "
                    "open-source vs proprietary split, inference cost deflation curve, "
                    "and regulatory tightening impact on adoption speed."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "trend_analysis",
            },
            {
                "task": (
                    "Produce a board-level report: AI Competitive Landscape 2025. "
                    "Include strategic position map, capability gap analysis vs. each "
                    "named competitor, and 5 recommended investment priorities."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "board_report",
            },
        ],
    },
    {
        "name": "Product Launch Risk Assessment",
        "description": (
            "A fintech is preparing to launch an embedded lending product in "
            "three new markets and must assess risks before go-live."
        ),
        "tasks": [
            {
                "task": (
                    "Collect current regulatory requirements for embedded consumer "
                    "lending in Brazil, Germany, and Singapore: licensing thresholds, "
                    "data localisation rules, and interest rate caps."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "data_collection",
            },
            {
                "task": (
                    "Evaluate operational, credit, FX, regulatory, and reputational "
                    "risks of the three-market expansion; score each risk on a 5x5 "
                    "impact-likelihood matrix and propose mitigations."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "risk_assessment",
            },
            {
                "task": (
                    "Build a 36-month revenue and loss forecast for the embedded "
                    "lending product in each market, including default rate assumptions, "
                    "provisioning schedules, and break-even timelines."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "forecasting",
            },
            {
                "task": (
                    "Identify competitive incumbents in embedded lending in Brazil, "
                    "Germany, and Singapore; map their distribution channels, "
                    "average APRs, and market share estimates."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "competitor_analysis",
            },
            {
                "task": (
                    "Write a formal risk assessment report for the board: executive "
                    "summary, market-by-market risk register, financial projections, "
                    "recommended go/no-go decision, and proposed monitoring KPIs."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "board_report",
            },
        ],
    },
    {
        "name": "Investor Series B Update",
        "description": (
            "A growth-stage startup is preparing its Series B investor update "
            "and needs compelling data storytelling plus the written narrative."
        ),
        "tasks": [
            {
                "task": (
                    "Analyse the last 12 months of ARR growth, net revenue retention, "
                    "payback period, and burn multiple; identify trend inflection points "
                    "and compare to Series B benchmarks from Bessemer's Cloud Index."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "trend_analysis",
            },
            {
                "task": (
                    "Produce revenue and ARR forecasts for the next 4 quarters under "
                    "three scenarios (base, bull, bear) using Monte Carlo simulation "
                    "with explicit assumptions for each driver."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "forecasting",
            },
            {
                "task": (
                    "Design a one-page visual dashboard for the investor update: "
                    "ARR waterfall chart, cohort NRR heatmap, CAC payback by channel, "
                    "and logo retention curve vs. industry median."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "data_visualization",
            },
            {
                "task": (
                    "Write the Q2 2025 investor update letter: highlights, KPI table, "
                    "product milestones, team additions, risks, and the Series B narrative "
                    "targeting $40M at a $200M post-money valuation."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "investor_update",
            },
        ],
    },
    {
        "name": "APAC Healthcare Market Entry",
        "description": (
            "A US-based digital health company is evaluating entry into Japan, "
            "South Korea, and Australia with a telehealth platform."
        ),
        "tasks": [
            {
                "task": (
                    "Research regulatory approval pathways for telehealth SaaS platforms "
                    "in Japan (PMDA), South Korea (MFDS), and Australia (TGA / AHPRA); "
                    "document timelines and key compliance requirements."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "web_research",
            },
            {
                "task": (
                    "Profile the top 3 digital health competitors in each APAC target "
                    "market: product scope, reimbursement status, physician network, "
                    "and 2024 reported patient volumes."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "competitor_analysis",
            },
            {
                "task": (
                    "Size the telehealth market in Japan, South Korea, and Australia "
                    "for 2025-2030: total TAM, SAM for primary care video consults, "
                    "and SOM achievable in year 3 with a $15M sales budget."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "market_sizing",
            },
            {
                "task": (
                    "Assess operational risks of entering all three markets "
                    "simultaneously vs. staged: localisation costs, partnership "
                    "dependency risks, and regulatory non-compliance scenarios."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "risk_assessment",
            },
            {
                "task": (
                    "Build a 5-year P&L model for APAC market entry: phased headcount "
                    "ramp, localisation capex, blended revenue per patient, and "
                    "breakeven analysis by country."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "financial_modeling",
            },
            {
                "task": (
                    "Write a comprehensive APAC market entry report for the board, "
                    "covering market opportunity, competitive landscape, regulatory "
                    "overview, financial projections, and recommended entry sequence."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "market_report",
            },
        ],
    },
    {
        "name": "Cloud Security Audit",
        "description": (
            "An enterprise CISO has commissioned a security posture audit across "
            "the company's multi-cloud infrastructure before a SOC 2 Type II audit."
        ),
        "tasks": [
            {
                "task": (
                    "Gather structured data on all AWS, Azure, and GCP resources: "
                    "IAM policies, security group rules, public S3/Blob buckets, "
                    "and unpatched CVEs across EC2/VM fleets."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "data_collection",
            },
            {
                "task": (
                    "Identify root causes of the three security incidents from the "
                    "past year: the misconfigured S3 bucket exposure in March, "
                    "the compromised service account in July, and the cryptomining "
                    "event in November."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "root_cause_analysis",
            },
            {
                "task": (
                    "Assess and score the top 15 identified security risks across "
                    "network perimeter, identity & access, data protection, and "
                    "supply-chain categories; map to NIST CSF and CIS Controls v8."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "risk_assessment",
            },
            {
                "task": (
                    "Write the Cloud Security Audit technical report: executive "
                    "summary for CISO, detailed findings with CVSS scores, "
                    "remediation runbook with priority ordering, and SOC 2 "
                    "readiness gap analysis."
                ),
                "expected_agent": "writer_agent",
                "expected_skill": "technical_doc",
            },
        ],
    },
    {
        "name": "Social Sentiment & Sales Correlation",
        "description": (
            "A consumer brand wants to understand whether social media sentiment "
            "is a leading indicator of weekly sales and automate the monitoring."
        ),
        "tasks": [
            {
                "task": (
                    "Collect 6 months of Twitter/X, Reddit, and Instagram mentions "
                    "for the brand and its top 3 competitors; normalise by volume "
                    "and tag by product line and region."
                ),
                "expected_agent": "research_agent",
                "expected_skill": "data_collection",
            },
            {
                "task": (
                    "Run sentiment analysis on the 120,000 collected social posts: "
                    "compute daily net sentiment score, emotional breakdown "
                    "(joy/anger/fear/surprise), and identify top 10 viral events."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "sentiment_analysis",
            },
            {
                "task": (
                    "Perform Granger causality tests and cross-correlation analysis "
                    "between daily net sentiment and weekly same-store sales; "
                    "determine optimal lag window and predictive R-squared."
                ),
                "expected_agent": "analysis_agent",
                "expected_skill": "statistical_analysis",
            },
            {
                "task": (
                    "Build a 13-week forward sales forecast model that incorporates "
                    "lagged sentiment scores, promotional calendar, and seasonal "
                    "indices; output confidence intervals at 80% and 95%."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "forecasting",
            },
            {
                "task": (
                    "Design an executive dashboard showing real-time sentiment trend, "
                    "leading-indicator alert zones, and projected sales range for "
                    "the next 4 weeks; include drill-down by product and region."
                ),
                "expected_agent": "data_agent",
                "expected_skill": "data_visualization",
            },
        ],
    },
]
