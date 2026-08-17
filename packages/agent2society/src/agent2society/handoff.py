"""The Handoff envelope.

A bare task string answers "what to do next". A Handoff answers "what to
do next, why it exists, what's been done so far, what we assume to be
true, and what would make this require a human." That richer envelope is
what makes a multi-agent system legible -- to humans and to the next
agent in the chain.

Every call into `Mesh.run()` operates on a Handoff internally. Callers
can pass a bare string (it gets wrapped automatically) or construct a
full Handoff to carry intent, assumptions, and an upstream decision
chain. The handoff `id` is what `mesh.explain(id)` keys against later.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


HumanReviewPredicate = Callable[[str], bool]


@dataclass(frozen=True)
class DecisionRecord:
    """One completed step in a handoff chain.

    When an upstream agent finishes work and the result feeds into a
    downstream handoff, the next Handoff carries a DecisionRecord
    summarising the upstream step. Downstream agents (and humans
    auditing the chain) can read why earlier choices were made instead
    of receiving raw text with no provenance.
    """

    agent: str
    skill: str
    summary: str
    confidence: Optional[float] = None
    at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "skill": self.skill,
            "summary": self.summary,
            "confidence": self.confidence,
            "at": self.at,
        }


@dataclass
class Handoff:
    """A task plus the meaning, intent, and provenance around it.

    Fields:
      task                 the literal action the chosen agent will run
      intent               the business goal in plain language
      assumptions          things we believe to be true entering this step
      prior                ordered chain of upstream DecisionRecords
      confidence_required  minimum score the routing decision must clear
                           before dispatch (fires `on_low_confidence` if
                           the chosen candidate's score is below this).
                           0.0 = no requirement.
      human_review_when    callable(result_text) -> bool. If it returns
                           True, the handoff is flagged for human review
                           and the `on_human_review` hook fires.
      id                   stable id; what `mesh.explain(id)` keys on
    """

    task: str
    intent: str = ""
    assumptions: List[str] = field(default_factory=list)
    prior: List[DecisionRecord] = field(default_factory=list)
    confidence_required: float = 0.0
    human_review_when: Optional[HumanReviewPredicate] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_string(cls, task: str) -> "Handoff":
        """Wrap a bare task string. Used internally so `mesh.run("...")`
        still works without forcing every caller to construct a Handoff."""
        return cls(task=task)

    def extend(
        self,
        *,
        agent: str,
        skill: str,
        summary: str,
        confidence: Optional[float] = None,
        next_task: Optional[str] = None,
        next_intent: Optional[str] = None,
    ) -> "Handoff":
        """Produce the next handoff in the chain, carrying this one's
        DecisionRecord forward. Pass `next_task` to change what the
        downstream agent will run; otherwise the task text is reused."""
        return Handoff(
            task=next_task if next_task is not None else self.task,
            intent=next_intent if next_intent is not None else self.intent,
            assumptions=list(self.assumptions),
            prior=list(self.prior)
            + [DecisionRecord(agent=agent, skill=skill, summary=summary, confidence=confidence)],
            confidence_required=self.confidence_required,
            human_review_when=self.human_review_when,
            id=uuid.uuid4().hex[:12],
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "intent": self.intent,
            "assumptions": list(self.assumptions),
            "prior": [p.to_dict() for p in self.prior],
            "confidence_required": self.confidence_required,
            "has_human_review_predicate": self.human_review_when is not None,
            "metadata": dict(self.metadata),
        }
