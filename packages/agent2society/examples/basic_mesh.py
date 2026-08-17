"""Smoke-test example: a three-agent society wrapped from local objects.

Run from the repo root:

    python examples/basic_mesh.py

It demonstrates the v1.5 surface:
  * adding agents from native objects
  * routing a few tasks via a deterministic capability graph
  * the conformance guardrail catching a denied task (no LLM in the loop)
  * a `Handoff` envelope carrying intent + assumptions
  * `society.explain(handoff.id)` printing the human-readable routing rationale
  * `society.report()` summarising telemetry
"""
from __future__ import annotations

from agent2society import Society, Handoff


class Researcher:
    name = "research-agent"
    description = "Searches the web and summarises sources."
    skills = [
        {
            "id": "web_research",
            "name": "Web Research",
            "description": "Search the web, gather sources, summarise findings.",
            "tags": ["research", "web", "search", "sources"],
        }
    ]

    def __call__(self, task: str) -> str:
        return f"[research findings for] {task}"


class Writer:
    name = "writer-agent"
    description = "Drafts executive memos from notes."
    skills = [
        {
            "id": "exec_memo",
            "name": "Executive Memo",
            "description": "Draft an executive memo from notes or findings.",
            "tags": ["writing", "memo", "exec", "draft", "business"],
        }
    ]

    def run(self, task: str) -> str:
        return f"[memo draft]\n{task}\n\n-- end memo"


class Coder:
    name = "coder-agent"
    description = "Writes Python code."
    skills = [
        {
            "id": "python_code",
            "name": "Write Python",
            "description": "Write Python functions, refactor, debug.",
            "tags": ["code", "python", "programming"],
        }
    ]

    def invoke(self, task: str) -> str:
        return f"```python\n# {task}\ndef stub():\n    pass\n```"


def main() -> None:
    society = Society()
    society.add(Researcher())
    society.add(Writer())
    society.add(Coder())

    # Tighten the writer beyond its card.
    society.boundary("writer-agent", deny=["financial-data"])

    # Wire a governance hook -- detection-only, never blocks dispatch.
    society.on_low_confidence(
        lambda exp: print(f"  ! low confidence ({exp.confidence:.2f}) on {exp.task!r}"),
        threshold=0.5,
    )

    print("Society agents:", society.agents())
    print()

    # Bare strings still work (backwards-compat).
    for t in [
        "Research Q3 customer churn drivers from the web",
        "Write a Python function to dedupe a list of dicts",
    ]:
        print(f">>> {t}")
        print(society.run(t))
        print()

    # Handoff envelope: carries intent, assumptions, and gives you a stable
    # id you can use to recall the full routing rationale later.
    h = Handoff(
        task="Draft an executive memo on customer churn",
        intent="prep the Q3 board pack",
        assumptions=["churn data is final", "memo is for board, not customers"],
    )
    print(f">>> {h.task}")
    print(society.run(h))
    print()

    exp = society.explain(h.id)
    print("--- routing explanation ---")
    print(exp.render())
    print()

    # Conformance guardrail: writer's boundary denies 'financial-data'.
    society_lenient = Society(strict=False)
    society_lenient.add(Writer())
    society_lenient.boundary("writer-agent", deny=["financial-data"])
    h_blocked = Handoff(task="Draft an exec memo including financial-data figures")
    out = society_lenient.run(h_blocked)
    exp_blocked = society_lenient.explain(h_blocked.id)
    print(">>> blocked task")
    print(f"output (empty): {out!r}")
    print(f"blocked_reason: {exp_blocked.blocked_reason}")
    print()

    society.report()


if __name__ == "__main__":
    main()
