"""compliance_reviewer_agent: A2A worker on port 5007."""
from comparisons.v3.agents import AGENTS_V3
from comparisons.v3.agents._shared import run_worker_server

NAME = "compliance_reviewer_agent"
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
