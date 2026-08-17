"""news_sentiment_agent: A2A worker on port 5002."""
from comparisons.v3.agents import AGENTS_V3
from comparisons.v3.agents._shared import run_worker_server

NAME = "news_sentiment_agent"
CFG = AGENTS_V3[NAME]


def main() -> None:
    run_worker_server(
        agent_name=NAME,
        port=CFG["port"],
        description=CFG["description"],
        skills=CFG["skills"],
    )


if __name__ == "__main__":
    main()
