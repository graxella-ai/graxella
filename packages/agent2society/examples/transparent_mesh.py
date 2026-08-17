"""v1.5 transparency surface: explanations, handoff chains, governance.

Run from the repo root:

    python examples/transparent_mesh.py

What this shows that `basic_mesh.py` does not:

  1. SelfAssessment on an agent card -- the agent's own declared limits,
     surfaced into the routing explanation.
  2. A two-step handoff chain (research -> writer) where the second
     handoff carries the first decision as `prior`.
  3. A low-confidence hook that fires when a routing score is below the
     handoff's confidence_required.
  4. A human-review hook that fires when the result text matches a
     predicate the caller supplied.
  5. ConflictDetector + on_conflict firing when the same task gets
     routed to different agents across runs.

All governance is detection-only -- nothing here blocks or auto-corrects.
"""
from __future__ import annotations

from agent2society import Society, Handoff, RoutingExplanation


# --- agents ---------------------------------------------------------------


class Researcher:
    name = "research-agent"
    description = "Searches and summarises sources."
    skills = [
        {
            "id": "web_research",
            "name": "Web Research",
            "description": "Search the web, gather sources, summarise findings.",
            "tags": ["research", "web", "search", "sources", "drivers"],
        }
    ]

    def __call__(self, task: str) -> str:
        return "found 3 churn drivers: pricing, onboarding friction, support latency"


# A card *with* a SelfAssessment block. The writer declares its own
# limits; agent2society surfaces them in every routing explanation that picks it.
WRITER_CARD = {
    "name": "writer-agent",
    "url": "local://writer",
    "description": "Drafts executive memos from notes.",
    "skills": [
        {
            "id": "exec_memo",
            "name": "Executive Memo",
            "description": "Draft an executive memo from notes or findings.",
            "tags": ["writing", "memo", "exec", "draft", "business"],
        }
    ],
    "selfAssessment": {
        "confidenceModel": "tfidf_score",
        "knownLimitations": ["English only", "max ~400 words per memo"],
        "outOfScope": ["legal opinion", "binding financial guidance"],
        "escalateWhen": ["any quantitative claim cited without source"],
    },
}


def writer_handler(_url, payload):
    msg_id = payload.get("id", "0")
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "parts": [
                {
                    "kind": "text",
                    "text": (
                        "MEMO: Q3 churn driven by pricing, onboarding friction, "
                        "and support latency. Recommend tightening onboarding."
                    ),
                }
            ]
        },
    }


# --- demo -----------------------------------------------------------------


def main() -> None:
    society = Society()
    society.add(Researcher())
    writer_card = society.add(WRITER_CARD)
    society._local.register(writer_card.url, writer_handler)

    # --- governance hooks: all side effects, none can block dispatch -----
    low_conf_log: list = []
    review_log: list = []
    conflict_log: list = []

    society.on_low_confidence(
        lambda exp: low_conf_log.append((exp.task, exp.confidence)),
        threshold=0.8,
    )
    society.on_human_review(lambda exp, text: review_log.append(text))
    society.on_conflict(lambda c: conflict_log.append(c))

    # --- step 1: research handoff with intent ----------------------------
    h1 = Handoff(
        task="Research Q3 customer churn drivers",
        intent="prep the Q3 board pack",
        assumptions=["churn data through end-of-quarter is final"],
        confidence_required=0.5,
    )
    research_result = society.run(h1)
    print(f"[step 1] research-agent ->\n  {research_result}\n")

    # --- step 2: handoff chain. Carries the upstream decision forward. ---
    h2 = h1.extend(
        agent="research-agent",
        skill="web_research",
        summary="found 3 churn drivers",
        confidence=0.62,
        next_task="Draft an executive memo on Q3 customer churn drivers",
    )
    # Set a predicate that flags anything quantitative for human review.
    h2 = Handoff(
        task=h2.task,
        intent=h2.intent,
        assumptions=h2.assumptions,
        prior=h2.prior,
        confidence_required=0.5,
        human_review_when=lambda text: "Q3" in text and "%" not in text,
    )
    memo = society.run(h2)
    print(f"[step 2] writer-agent ->\n  {memo}\n")

    # --- routing explanations -------------------------------------------
    exp = society.explain(h2.id)
    print("--- routing explanation for the memo ---")
    print(exp.render())
    print()
    print("(notice the agent self-caveats above -- the writer's card declares")
    print(" its own limits and agent2society surfaces them on every decision.)")
    print()

    # --- governance signal: same task, different agent ------------------
    # Splice a spoof explanation in to demonstrate the conflict detector.
    spoof = RoutingExplanation(
        handoff_id="spoof",
        task=h2.task,
        intent="",
        chosen_agent="research-agent",
        chosen_skill="web_research",
        rationale="r",
        features_fired={},
        alternatives=[],
        confidence=0.4,
        agent_self_caveats=[],
        blocked_reason=None,
        prior_chain=[],
        assumptions=[],
    )
    society._store_explanation("spoof", spoof)
    society._maybe_fire_governance()

    # --- summary --------------------------------------------------------
    print("--- governance fired ---")
    print(f"low-confidence triggers: {len(low_conf_log)}")
    for task, conf in low_conf_log:
        print(f"  - {task!r} confidence={conf:.3f}")
    print(f"human-review triggers:   {len(review_log)}")
    for text in review_log:
        print(f"  - flagged: {text[:60]!r}")
    print(f"conflict triggers:       {len(conflict_log)}")
    for c in conflict_log:
        print(f"  - {c.kind}: {c.detail}")
    print()

    society.report()


if __name__ == "__main__":
    main()
