"""Session-level trace: joins RoutingExplanation + RoutingRecord into a
unified, human-readable timeline per Society.run() call.

This is NOT a sampling tracer or an OpenTelemetry exporter. It is a
deterministic audit log -- every routing decision in the session,
exactly as it happened, in the order it happened.

Usage::

    s = Society(strict=False)
    s.add(...)
    tracer = SessionTracer(s)

    s.run(Handoff(task="classify this document"))
    s.run(Handoff(task="summarise the result"))

    tracer.render()        # print to stdout
    events = tracer.events()  # list[TraceEvent] -- iterate or inspect
    tracer.to_dict()       # JSON-serialisable snapshot
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .mesh import Mesh


@dataclass
class TraceEvent:
    """One entry in the session timeline -- one Society.run() call."""

    seq: int                           # 1-based call index
    handoff_id: str
    task: str
    intent: str
    chosen_agent: Optional[str]
    chosen_skill: Optional[str]
    score: Optional[float]
    margin: float
    flags: List[str]
    rationale: str
    candidates_considered: int
    dispatched: bool
    fallbacks: List[Dict[str, Any]]
    request_tokens: int
    response_tokens: int
    error: Optional[str]
    agent_caveats: List[str]
    assumptions: List[str]
    prior_steps: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "handoff_id": self.handoff_id,
            "task": self.task,
            "intent": self.intent,
            "chosen_agent": self.chosen_agent,
            "chosen_skill": self.chosen_skill,
            "score": self.score,
            "margin": self.margin,
            "flags": self.flags,
            "rationale": self.rationale,
            "candidates_considered": self.candidates_considered,
            "dispatched": self.dispatched,
            "fallbacks": self.fallbacks,
            "request_tokens": self.request_tokens,
            "response_tokens": self.response_tokens,
            "error": self.error,
            "agent_caveats": self.agent_caveats,
            "assumptions": self.assumptions,
            "prior_steps": self.prior_steps,
        }

    def render(self, *, width: int = 80) -> str:
        """Single-event ASCII block."""
        bar = "-" * width
        lines: List[str] = [bar]
        hdr = f"[{self.seq}] {self.task}"
        if len(hdr) > width:
            hdr = hdr[: width - 3] + "..."
        lines.append(hdr)
        lines.append(f"    handoff : {self.handoff_id}")
        if self.intent:
            lines.append(f"    intent  : {self.intent}")
        if self.prior_steps:
            lines.append(f"    chain   : {self.prior_steps} prior step(s)")
        if self.assumptions:
            for a in self.assumptions:
                lines.append(f"    assume  : {a}")

        if self.chosen_agent:
            score_str = f"{self.score:.4f}" if self.score is not None else "n/a"
            lines.append(
                f"    routed  : {self.chosen_agent} :: {self.chosen_skill}"
                f"  score={score_str}  margin={self.margin:.4f}"
            )
        else:
            lines.append("    routed  : NONE (unroutable)")

        if self.flags:
            lines.append(f"    flags   : {', '.join(self.flags)}")

        lines.append(
            f"    consider: {self.candidates_considered} candidate(s)"
            f"  dispatched={'yes' if self.dispatched else 'no'}"
        )
        for fb in self.fallbacks:
            lines.append(
                f"    fallback: {fb.get('agent')} :: {fb.get('skill')}"
                f"  ({fb.get('reason', '')})"
            )

        if self.error:
            lines.append(f"    ! error : {self.error}")

        lines.append(
            f"    tokens  : req~{self.request_tokens}"
            f"  resp~{self.response_tokens}"
            f"  total~{self.request_tokens + self.response_tokens}"
        )

        wrap = textwrap.fill(
            self.rationale, width=width - 12,
            initial_indent="    why     : ",
            subsequent_indent="              ",
        )
        lines.append(wrap)

        if self.agent_caveats:
            lines.append("    caveats :")
            for c in self.agent_caveats:
                lines.append(f"              - {c}")

        return "\n".join(lines)


class SessionTracer:
    """Unified trace over all Society.run() calls in a session.

    Joins the Society's TelemetrySink (dispatch records) with its
    ExplanationStore (routing explanations) into one ordered list of
    TraceEvents.

    The Society is referenced, not copied -- calling `tracer.events()`
    after more `society.run()` calls picks up the new data automatically.
    """

    def __init__(self, society: "Mesh") -> None:
        self._society = society

    # ---- core output -----------------------------------------------------

    def events(self) -> List[TraceEvent]:
        """Ordered list of TraceEvents, one per Society.run() call."""
        records = self._society.telemetry.records
        events: List[TraceEvent] = []
        for i, rec in enumerate(records, 1):
            exp = (
                self._society.explain(rec.handoff_id)
                if rec.handoff_id
                else None
            )
            events.append(
                TraceEvent(
                    seq=i,
                    handoff_id=rec.handoff_id or "",
                    task=rec.task,
                    intent=rec.intent or "",
                    chosen_agent=rec.chosen_agent,
                    chosen_skill=rec.chosen_skill,
                    score=rec.score,
                    margin=exp.margin if exp else 0.0,
                    flags=list(exp.flags) if exp else [],
                    rationale=exp.rationale if exp else "",
                    candidates_considered=len(rec.candidates),
                    dispatched=rec.dispatched,
                    fallbacks=list(rec.fallbacks),
                    request_tokens=rec.request_tokens,
                    response_tokens=rec.response_tokens,
                    error=rec.error,
                    agent_caveats=list(exp.agent_self_caveats) if exp else [],
                    assumptions=list(exp.assumptions) if exp else [],
                    prior_steps=len(exp.prior_chain) if exp else 0,
                )
            )
        return events

    def summary(self) -> Dict[str, Any]:
        """Session-level aggregates."""
        evts = self.events()
        dispatched = [e for e in evts if e.dispatched]
        flagged = [e for e in evts if e.flags]
        low_margin = [e for e in evts if "LOW_MARGIN" in e.flags]
        ood = [e for e in evts if "OOD" in e.flags]
        ambiguous = [e for e in evts if "VECTOR_AMBIGUITY" in e.flags]
        scores = [e.score for e in evts if e.score is not None]
        return {
            "total_calls": len(evts),
            "dispatched": len(dispatched),
            "unrouted": len(evts) - len(dispatched),
            "flagged": len(flagged),
            "low_margin": len(low_margin),
            "ood": len(ood),
            "vector_ambiguity": len(ambiguous),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "total_tokens": sum(e.request_tokens + e.response_tokens for e in evts),
        }

    # ---- rendering -------------------------------------------------------

    def render(self, *, width: int = 80) -> str:
        """Full session timeline printed to a string."""
        lines: List[str] = []
        lines.append("=" * width)
        lines.append("agent2society  SESSION TRACE")
        lines.append("=" * width)
        evts = self.events()
        if not evts:
            lines.append("(no events recorded yet)")
        else:
            for e in evts:
                lines.append(e.render(width=width))
        # summary footer
        s = self.summary()
        lines.append("=" * width)
        lines.append(
            f"SUMMARY  calls={s['total_calls']}  dispatched={s['dispatched']}"
            f"  unrouted={s['unrouted']}  flagged={s['flagged']}"
        )
        lines.append(
            f"         low_margin={s['low_margin']}  ood={s['ood']}"
            f"  ambiguous={s['vector_ambiguity']}"
            f"  avg_score={s['avg_score']}"
            f"  total_tokens~{s['total_tokens']}"
        )
        lines.append("=" * width)
        return "\n".join(lines)

    def print(self, *, width: int = 80) -> None:
        """Convenience: render and print."""
        print(self.render(width=width))

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable snapshot of the full trace."""
        return {
            "summary": self.summary(),
            "events": [e.to_dict() for e in self.events()],
        }
