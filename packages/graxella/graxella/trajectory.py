"""graxella.trajectory — the bounded multi-hop runtime (Phase 2, 2-1/2-2).

Where MAST's two most frequent failure modes live: step repetition
(FM-1.3, 15.7% of observed failures) and termination-unawareness
(FM-1.5, 12.4%). The loop is route → execute → assess → (handoff |
complete | escalate), with four containments:

  * loop detection   — a repeated (agent, response-state) signature is
                       stopped and escalated, never re-run (FM-1.3)
  * budgets          — hops, tokens, wallclock; exhaustion escalates
                       instead of running away (FM-1.5)
  * typed re-route   — agents hand off with an explicit marker line
                       ``HANDOFF: <agent> :: <task>`` (task 2-2); every
                       hop goes through route(), so every hop carries a
                       decision, an outcome, and an explanation
  * escalation       — dead ends become ledger signals + tracer events
                       for a human, never silent retries

The trajectory itself is a first-class ledger object (predicate=
"trajectory", derived_from=its hop decisions) — chain-level evidence
for the chain-healing miner (task 2-7) and MAST FM-2.1: state lives in
the ledger, not in chat scrollback.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

HANDOFF_RE = re.compile(r"HANDOFF:\s*([A-Za-z0-9_]+)\s*::\s*(.+)", re.IGNORECASE)


class TrajectoryBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_hops: int = Field(default=5, ge=1)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    max_wallclock_s: Optional[float] = Field(default=None, gt=0)


class Hop(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    agent: Optional[str]
    decision_id: str
    ok: bool
    task: str
    response_head: str
    tools_used: list[str] = []
    tokens: int = 0


class TrajectoryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    task: str
    status: str            # completed | loop_detected | budget_exhausted | failed
    hops: tuple[Hop, ...]
    final_response: str
    escalated: bool = False
    assertion_id: Optional[str] = None

    @property
    def n_hops(self) -> int:
        return len(self.hops)


def run_trajectory(app: Any, task: str, *,
                   budget: TrajectoryBudget | None = None) -> TrajectoryResult:
    """Drive ``app`` (an InstrumentedApp) through a bounded multi-hop
    trajectory. Every hop is a full route() — decision, typed outcome,
    explanation, recall — and the whole chain lands in the ledger."""
    budget = budget or TrajectoryBudget()
    tid = f"trj_{uuid.uuid4().hex[:12]}"
    t0 = time.perf_counter()
    hops: list[Hop] = []
    seen_signatures: set[tuple] = set()
    current = task
    status = "completed"
    escalated = False
    tokens_total = 0
    response = ""

    while True:
        result, aid = app.route(current)
        response = result.response or ""
        ok = result.chosen_agent is not None and not response.startswith("[error]")
        hop_tokens = (result.tokens_in or 0) + (result.tokens_out or 0)
        tokens_total += hop_tokens
        hops.append(Hop(seq=len(hops) + 1, agent=result.chosen_agent,
                        decision_id=aid, ok=ok, task=current[:200],
                        response_head=response[:200],
                        tools_used=list(getattr(result, "tools_used", []) or []),
                        tokens=hop_tokens))

        if not ok:
            status = "failed"
            escalated = True
            break

        # FM-1.3: an identical (agent, response-state) pair means the
        # chain is spinning — stop it before it burns another hop.
        sig = (result.chosen_agent, hash(response[:200]))
        if sig in seen_signatures:
            status = "loop_detected"
            escalated = True
            break
        seen_signatures.add(sig)

        # 2-2: typed handoff marker → audited re-route (or completion).
        m = HANDOFF_RE.search(response)
        if m is None:
            status = "completed"          # FM-1.5: no handoff = declared done
            break
        target, subtask = m.group(1).lower(), m.group(2).strip()
        if target not in set(app.society.agents()):
            app.tracer.record("orchestrator", "degradation.handoff_unknown",
                              {"trajectory_id": tid, "target": target,
                               "hop": len(hops)})
            status = "completed"          # unknown peer: finish, loudly
            break

        # FM-1.5: budgets contain runaway chains — exhaustion escalates.
        if len(hops) >= budget.max_hops \
                or (budget.max_tokens and tokens_total >= budget.max_tokens) \
                or (budget.max_wallclock_s
                    and time.perf_counter() - t0 >= budget.max_wallclock_s):
            status = "budget_exhausted"
            escalated = True
            break
        current = subtask

    if escalated:
        sig_id = app.memory.record_signal(
            kind="trajectory_escalation",
            decision_id=hops[-1].decision_id,
            detail={"trajectory_id": tid, "status": status,
                    "domain": app.domain or app.memory.namespace,
                    "agent": hops[-1].agent, "hops": len(hops)},
        )
        app.tracer.record("orchestrator", "trajectory.escalated",
                          {"trajectory_id": tid, "status": status,
                           "signal_id": sig_id, "hops": len(hops)})

    # The chain is a ledger object: derived_from = every hop decision.
    aid = app.memory._client.observe(
        json.dumps({"trajectory_id": tid, "task": task[:300], "status": status,
                    "hops": [{"seq": h.seq, "agent": h.agent,
                              "decision_id": h.decision_id, "ok": h.ok}
                             for h in hops],
                    "tokens_total": tokens_total,
                    "wallclock_ms": round((time.perf_counter() - t0) * 1000, 1)},
                   sort_keys=True),
        subject=tid,
        predicate="trajectory",
        object=status,
        confidence=1.0,
        source_id="trajectory-runtime",
        derived_from=tuple(h.decision_id for h in hops),
    )
    return TrajectoryResult(id=tid, task=task, status=status,
                            hops=tuple(hops), final_response=response,
                            escalated=escalated, assertion_id=aid)


__all__ = ["TrajectoryBudget", "Hop", "TrajectoryResult", "run_trajectory",
           "HANDOFF_RE"]
