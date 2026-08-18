"""Task 1-8 — the human review loop, cross-process by construction:
decisions live in the ledger, so a fresh gate (or the CLI in another
process) honors what the operator recorded."""
from __future__ import annotations

import pytest

from graxella.beliefs import Memory
from graxella.cli.main import main as cli_main
from graxella.gate.evidence import EvidenceGate, GateDecision, pending_from_ledger
from graxella.gate.spec import (ArtifactKind, EvidenceRole, Proposal,
                                ProposalStatus, TargetScope)


@pytest.fixture()
def memory(tmp_path):
    return Memory.sqlite(str(tmp_path / "m.db"), agent_id="rev",
                         namespace="weather")


def proposal():
    return Proposal(id="prop_review_01", kind=ArtifactKind.TOOL_BINDING,
                    target=TargetScope(domain="weather", tool="get_weather"),
                    payload={"replace_skill": "get_weather",
                             "with_skill": "fetch_forecast"},
                    origin="miner:t")


def test_approve_survives_process_boundary(memory):
    gate1 = EvidenceGate(memory)
    v1, _ = gate1.decide(proposal())
    assert v1.decision is GateDecision.NEEDS_HUMAN
    assert pending_from_ledger(memory)[0]["proposal_id"] == "prop_review_01"

    gate1.approve("prop_review_01", by="sridhar", note="looks right")

    gate2 = EvidenceGate(memory)              # a "different process"
    v2, updated = gate2.decide(proposal())
    assert v2.decision is GateDecision.AUTO_APPROVE
    assert "approved by sridhar" in v2.reason
    assert updated.status is ProposalStatus.APPROVED
    assert any(c.role is EvidenceRole.OPERATOR_DECISION
               for c in updated.evidence)
    assert pending_from_ledger(memory) == []   # queue cleared


def test_human_rejection_is_always_honored(memory):
    gate = EvidenceGate(memory)
    gate.decide(proposal())
    gate.reject("prop_review_01", by="sridhar", note="wrong target")
    v, updated = EvidenceGate(memory).decide(proposal())
    assert v.decision is GateDecision.AUTO_REJECT
    assert "rejected by sridhar" in v.reason
    assert updated.status is ProposalStatus.REJECTED


def test_hard_block_beats_human_approval(memory):
    gate = EvidenceGate(memory, hard_blocks=(lambda p: "forbidden",))
    gate.approve("prop_review_01", by="sridhar")
    v, updated = gate.decide(proposal())
    assert v.decision is GateDecision.AUTO_REJECT   # constitution over people
    assert updated.status is ProposalStatus.REJECTED


def test_cli_review_loop_end_to_end(memory, tmp_path, capsys):
    """An operator runs the whole loop without touching Python."""
    EvidenceGate(memory).decide(proposal())
    db = str(tmp_path / "m.db")
    base = ["--db", db, "--agent", "rev", "--namespace", "weather"]

    assert cli_main(["gate", "list", *base]) == 0
    out = capsys.readouterr().out
    assert "prop_review_01" in out and "cold start" in out

    assert cli_main(["gate", "why", *base, "--id", "prop_review_01"]) == 0
    assert "NEEDS_HUMAN" in capsys.readouterr().out

    assert cli_main(["gate", "approve", *base, "--id", "prop_review_01",
                     "--as", "sridhar", "--note", "ship it"]) == 0
    assert "approved" in capsys.readouterr().out

    assert cli_main(["gate", "list", *base]) == 0
    assert "no proposals awaiting review" in capsys.readouterr().out

    # And the recorded decision folds into the next decide().
    v, _ = EvidenceGate(memory).decide(proposal())
    assert v.decision is GateDecision.AUTO_APPROVE
