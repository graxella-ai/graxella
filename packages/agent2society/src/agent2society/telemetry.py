"""Telemetry: routing decisions, dispatches, violations, token estimates.

`Mesh.report()` reads this to give the developer a quick view of where
agent2society earned its keep. Token counts are estimates unless the underlying
agent surfaces real usage in its response.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def estimate_tokens(text: str) -> int:
    """Rough estimate: 1 token ~= 4 chars. Fine for relative comparisons."""
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass
class RoutingRecord:
    task: str
    chosen_agent: Optional[str]
    chosen_skill: Optional[str]
    score: Optional[float]
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    dispatched: bool = False
    response_text: Optional[str] = None
    response_tokens: int = 0
    request_tokens: int = 0
    violation: Optional[Dict[str, Any]] = None
    fallbacks: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    handoff_id: Optional[str] = None
    intent: str = ""
    explanation_id: Optional[str] = None

    def total_tokens(self) -> int:
        return self.request_tokens + self.response_tokens


class TelemetrySink:
    def __init__(self) -> None:
        self.records: List[RoutingRecord] = []

    def add(self, record: RoutingRecord) -> None:
        self.records.append(record)

    # ---- aggregations ---------------------------------------------------
    def total_tokens(self) -> int:
        return sum(r.total_tokens() for r in self.records)

    def violations(self) -> List[RoutingRecord]:
        return [r for r in self.records if r.violation is not None]

    def summary(self) -> Dict[str, Any]:
        return {
            "dispatches": sum(1 for r in self.records if r.dispatched),
            "tasks": len(self.records),
            "violations": len(self.violations()),
            "total_tokens": self.total_tokens(),
        }

    # ---- pretty printing ------------------------------------------------
    def render(self) -> str:
        s = self.summary()
        lines: List[str] = []
        lines.append("agent2society report")
        lines.append("=" * 60)
        lines.append(
            f"tasks={s['tasks']}  dispatches={s['dispatches']}  "
            f"violations={s['violations']}  total_tokens~{s['total_tokens']}"
        )
        lines.append("")
        for i, r in enumerate(self.records, 1):
            hid = f" [{r.handoff_id}]" if r.handoff_id else ""
            head = f"[{i}]{hid} {r.task!r}"
            if len(head) > 78:
                head = head[:75] + "..."
            lines.append(head)
            if r.intent:
                lines.append(f"    intent: {r.intent}")
            if r.violation:
                lines.append(
                    f"    ! BLOCKED on {r.violation.get('agent')}: "
                    f"{r.violation.get('reason')}"
                )
            if r.chosen_agent:
                lines.append(
                    f"    -> {r.chosen_agent} :: {r.chosen_skill} "
                    f"(score={r.score:.3f})"
                )
            for fb in r.fallbacks:
                lines.append(
                    f"    .. fell back from {fb.get('agent')} "
                    f"({fb.get('reason')})"
                )
            if r.error:
                lines.append(f"    ! error: {r.error}")
            if r.dispatched:
                lines.append(
                    f"    tokens: req~{r.request_tokens} "
                    f"resp~{r.response_tokens}"
                )
            if r.explanation_id:
                lines.append(f"    explain: society.explain({r.explanation_id!r})")
        return "\n".join(lines)
