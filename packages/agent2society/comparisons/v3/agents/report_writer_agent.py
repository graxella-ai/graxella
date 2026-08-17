"""report_writer_agent: A2A worker on port 5006."""
from comparisons.v3.agents import AGENTS_V3
from comparisons.v3.agents._shared import run_worker_server

NAME = "report_writer_agent"
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
