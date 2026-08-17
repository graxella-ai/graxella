"""Head-to-head: agent2society vs. real LangGraph supervisor.

Runs the default complex multi-step scenarios through:
  * a real `langgraph.graph.StateGraph` supervisor with a counted fake
    chat model — coordination tokens measured on the actual prompts
    LangGraph sends.
  * agent2society, given the pre-decomposed sub-tasks (v1 design: user owns
    decomposition).

Requires:
    pip install agent2society[bench]
    pip install langgraph langchain-core
"""
from __future__ import annotations

from agent2society.bench import default_scenarios
from agent2society.bench.multistep import MultiStepBench
from agent2society.bench.tasks import default_mesh_cards


def main() -> None:
    bench = MultiStepBench(
        cards=default_mesh_cards(),
        scenarios=default_scenarios(),
    )
    result = bench.run()
    print(result.render())


if __name__ == "__main__":
    main()
