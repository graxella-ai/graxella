"""v3 worker agents. Each is its own A2A server, ports 5001-5007."""

AGENTS_V3 = {
    "market_data_agent": {
        "port": 5001,
        "description": "Fetches and analyses structured market data: prices, ratios, volumes, dividends",
        "skills": [
            ("fetch_prices",          "Retrieves recent price history and current quote for a security"),
            ("fetch_ratios",          "Computes P/E, P/B, debt-to-equity and other financial ratios"),
            ("fetch_volume_trends",   "Analyses trading volume patterns and unusual activity"),
            ("fetch_dividends",       "Reports dividend history and yield calculations"),
        ],
        "boundary_deny": ["material_nonpublic_information", "raw_pii"],
    },
    "news_sentiment_agent": {
        "port": 5002,
        "description": "Searches news and analyses sentiment, events, and narratives",
        "skills": [
            ("news_search",           "Searches news sources for stories about a company or sector"),
            ("sentiment_classify",    "Classifies tone and sentiment of text passages"),
            ("event_detection",       "Identifies material events like earnings, lawsuits, executive changes"),
            ("narrative_extraction",  "Extracts dominant narratives and themes from press coverage"),
        ],
        "boundary_deny": ["material_nonpublic_information", "raw_pii"],
    },
    "financial_analyst_agent": {
        "port": 5003,
        "description": "Quantitative valuation: DCF, ratio analysis, scenario modelling, peer comparison",
        "skills": [
            ("dcf_valuation",         "Builds a discounted cash flow valuation with sensitivity bands"),
            ("ratio_analysis",        "Computes and interprets fundamental financial ratios"),
            ("scenario_modeling",     "Constructs bear/base/bull scenarios with assumptions"),
            ("peer_comparison",       "Compares a company to its peer group on key metrics"),
        ],
        "boundary_deny": ["forward_looking_statements", "raw_pii"],
    },
    "risk_assessor_agent": {
        "port": 5004,
        "description": "Risk assessment: market, credit, regulatory, ESG, geopolitical",
        "skills": [
            ("market_risk",           "Assesses beta, volatility, drawdown and tail risk"),
            ("credit_risk",           "Evaluates debt capacity, default probability, covenant risk"),
            ("regulatory_risk",       "Identifies regulatory exposure and compliance gaps"),
            ("esg_risk",              "Screens environmental, social, governance issues"),
            ("geopolitical_risk",     "Maps exposure to country, conflict, sanctions risk"),
        ],
        "boundary_deny": [],
    },
    "competitor_intel_agent": {
        "port": 5005,
        "description": "Competitive intelligence: landscape, market share, product comparison, strategy",
        "skills": [
            ("competitor_landscape",  "Maps the competitive landscape and key players in a sector"),
            ("market_share_analysis", "Analyses market share dynamics over time"),
            ("product_comparison",    "Compares product features, pricing, and positioning"),
            ("strategic_moves",       "Tracks acquisitions, partnerships, and strategic announcements"),
        ],
        "boundary_deny": ["material_nonpublic_information"],
    },
    "report_writer_agent": {
        "port": 5006,
        "description": "Drafts professional documents: investment memos, exec summaries, board briefs, client letters",
        "skills": [
            ("investment_memo",       "Drafts a long-form investment thesis memo"),
            ("exec_summary",          "Produces a one-page executive summary"),
            ("board_brief",           "Writes a board-ready briefing document"),
            ("client_letter",         "Drafts a client-facing communication"),
        ],
        "boundary_deny": ["raw_pii", "material_nonpublic_information"],
    },
    "compliance_reviewer_agent": {
        "port": 5007,
        "description": "Compliance: disclosure check, MNPI screening, restricted list, regulatory classification",
        "skills": [
            ("disclosure_check",          "Verifies required disclosures are present"),
            ("mnpi_screening",            "Screens content for material nonpublic information"),
            ("restricted_list_check",     "Checks names against firm restricted lists"),
            ("regulatory_classification", "Classifies content by applicable regulatory regime"),
        ],
        "boundary_deny": [],
    },
}
