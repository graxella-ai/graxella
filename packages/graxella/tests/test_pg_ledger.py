"""Task 3-2 — Postgres ledger backend.

mnema's store rides SQLModel/SQLAlchemy, so the "backend" is a URL:
``Memory(db_path="postgresql+psycopg://...")`` engages Postgres with the
same schema and the same test suite semantics.

Honesty contract: without a live Postgres these tests SKIP, never fake
green. Point GRAXELLA_PG_URL at a scratch database to run them for real:

    set GRAXELLA_PG_URL=postgresql+psycopg://user:pass@localhost/graxella
    uv run pytest packages/graxella/tests/test_pg_ledger.py

(also needs: uv pip install psycopg[binary])
"""
from __future__ import annotations

import os
import uuid

import pytest

from graxella.beliefs import Memory

PG_URL = os.environ.get("GRAXELLA_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="set GRAXELLA_PG_URL to run Postgres ledger tests")


@pytest.fixture()
def memory():
    ns = f"pgtest_{uuid.uuid4().hex[:8]}"      # isolated per test run
    return Memory(agent_id="pg", db_path=PG_URL, namespace=ns)


def test_url_passthrough_engages_postgres(memory):
    assert memory._client._store.engine.dialect.name == "postgresql"


def test_full_outcome_loop_on_postgres(memory):
    aid = memory.record_decision(decision_type="delegate", task="pg task",
                                 chosen="a::s", domain=memory.namespace)
    memory.record_outcome(decision_id=aid, ok=True, tokens_in=5,
                          domain=memory.namespace)
    rows = memory.beliefs(subject=aid, predicate="outcome")
    assert len(rows) == 1
    assert memory.outcome_stats()["total"]["count"] == 1


def test_gate_on_postgres(memory):
    from graxella.gate.evidence import EvidenceGate, GateDecision
    from graxella.gate.spec import ArtifactKind, Proposal, TargetScope

    gate = EvidenceGate(memory)
    p = Proposal(kind=ArtifactKind.TOOL_BINDING,
                 target=TargetScope(domain=memory.namespace, tool="t"),
                 payload={}, origin="miner:pg")
    v, _ = gate.decide(p)
    assert v.decision is GateDecision.NEEDS_HUMAN
    assert "NEEDS_HUMAN" in gate.why(p.id)
