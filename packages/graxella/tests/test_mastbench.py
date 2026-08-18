"""Task 2-8 — MAST replay harness, Step 1 (offline detection scoring)."""
from __future__ import annotations

from pathlib import Path

from graxella.mastbench import load, main, replay

FIXTURE = Path(__file__).parent / "fixtures" / "mast_sample.jsonl"


def test_fixture_detection_rates():
    report = replay(load(FIXTURE))
    assert report["modes"]["FM-1.3"] == {"labeled": 2, "detected": 2,
                                         "rate": 1.0}
    assert report["modes"]["FM-1.5"] == {"labeled": 1, "detected": 1,
                                         "rate": 1.0}
    assert report["modes"]["FM-2.6"] == {"labeled": 2, "detected": 2,
                                         "rate": 1.0}


def test_clean_traces_are_not_flagged():
    report = replay(load(FIXTURE))
    assert report["clean"] == {"labeled": 2, "flagged": 0,
                               "false_positive_rate": 0.0}


def test_honesty_disclaimer_is_structural():
    report = replay([])
    assert "NOT live prevention" in report["disclaimer"]


def test_cli_entry(capsys):
    assert main([str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert "FM-1.3: 2/2" in out
    assert "false-positive rate=0.0" in out
    assert "foreign traces" in out
