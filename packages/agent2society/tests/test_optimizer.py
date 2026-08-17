"""Tests for the v0.4 observation-driven optimizer.

The optimizer's contract:
  * deterministic given (graph, explanations, labels)
  * never mutates the society on `optimize()` -- only `apply_optimization`
  * backtest must reject regressions (any edit that breaks a previously-
    correct decision is dropped unless net wins > 0)
  * fixes a documented TF-IDF miss on the bench suite without breaking
    other labelled tasks
"""
from __future__ import annotations

from agent2society import Handoff, Society, OptimizationReport, SkillEdit
from agent2society.bench.tasks import default_mesh_cards, default_suite


def _seeded_society():
    s = Society(strict=False)
    for c in default_mesh_cards():
        s.add(c)
    return s


def _run_suite(s: Society):
    """Run every task in the default labeled suite, return list of
    (handoff_id, expected_agent, expected_skill) labels."""
    labels = []
    for spec in default_suite():
        h = Handoff(task=spec.task)
        s.run(h)
        labels.append((h.id, spec.expected_agent, spec.expected_skill))
    return labels


def test_optimize_is_pure_until_apply():
    """`optimize()` must not mutate the graph -- routing after optimize()
    (no apply) is identical to routing before."""
    s = _seeded_society()
    labels = _run_suite(s)

    # Snapshot of every skill's tags before optimize.
    before = {
        (n.name, sk.id): tuple(sk.tags)
        for n in s.graph.agents()
        for sk in n.card.skills
    }
    report = s.optimize(labels)
    after = {
        (n.name, sk.id): tuple(sk.tags)
        for n in s.graph.agents()
        for sk in n.card.skills
    }
    assert before == after, (
        "optimize() should not have mutated any skill tags; "
        "the only mutator is apply_optimization()"
    )
    assert isinstance(report, OptimizationReport)


def test_optimize_fixes_documented_correlations_miss():
    """The bench README documents a known TF-IDF miss: 'Compute correlations
    ...customer feature table' routes to translator-agent over analyst-agent.
    The optimizer should mine discriminative tokens that close this miss."""
    s = _seeded_society()
    labels = _run_suite(s)

    # Confirm the miss reproduces before we optimize -- otherwise the test
    # is asserting against a bug that's already gone.
    correlations_label = next(
        l for l in labels if "correlations" in s.explain(l[0]).task.lower()
    )
    exp = s.explain(correlations_label[0])
    assert exp.chosen_agent != "analyst-agent", (
        f"the documented miss did not reproduce: {exp.chosen_agent} was picked"
    )

    report = s.optimize(labels)
    accepted = report.accepted_edits

    # At least one accepted edit should target analyst-agent::data_analysis.
    analyst_edits = [
        e for e in accepted
        if e.agent == "analyst-agent" and e.skill_id == "data_analysis"
    ]
    assert analyst_edits, (
        f"expected at least one accepted edit on analyst-agent::data_analysis; "
        f"got {[(e.agent, e.skill_id) for e in accepted]}"
    )

    # Apply and re-route. The correlations task must now hit analyst-agent.
    applied = s.apply_optimization(report)
    assert applied >= 1

    h = Handoff(task=s.explain(correlations_label[0]).task)
    s.run(h)
    new_exp = s.explain(h.id)
    assert new_exp.chosen_agent == "analyst-agent", (
        f"after optimization, correlations task should route to analyst-agent; "
        f"got {new_exp.chosen_agent}"
    )


def test_optimize_is_deterministic():
    """Same graph + same labels -> same report (modulo backtest counts)."""
    s1 = _seeded_society()
    labels1 = _run_suite(s1)
    report1 = s1.optimize(labels1)

    s2 = _seeded_society()
    labels2 = _run_suite(s2)
    report2 = s2.optimize(labels2)

    # Edit identity must match (same agent/skill/add_tags tuples in same order).
    keys1 = tuple((e.agent, e.skill_id, e.add_tags) for e in report1.edits)
    keys2 = tuple((e.agent, e.skill_id, e.add_tags) for e in report2.edits)
    assert keys1 == keys2, (
        f"optimizer should be deterministic; got\n {keys1}\nvs\n {keys2}"
    )


def test_already_correct_labels_skipped():
    """A label whose past decision already matched should not generate an edit."""
    s = _seeded_society()
    # Use only tasks that the default scorer already gets right.
    correct_only = []
    for spec in default_suite():
        h = Handoff(task=spec.task)
        s.run(h)
        exp = s.explain(h.id)
        if exp.chosen_agent == spec.expected_agent and exp.chosen_skill == spec.expected_skill:
            correct_only.append((h.id, spec.expected_agent, spec.expected_skill))

    assert correct_only, "test setup error: no already-correct labels found"
    report = s.optimize(correct_only)
    assert report.misses_addressed == 0
    assert report.edits == ()
    assert report.labels_already_correct == len(correct_only)


def test_missing_explanation_is_reported_not_crashed():
    """A label whose handoff id was never seen on this society is counted,
    not exception-thrown."""
    s = _seeded_society()
    # Note: no .run() calls -- ledger is empty.
    report = s.optimize([("never-seen-id", "research-agent", "web_research")])
    assert report.labels_missing_explanations == 1
    assert report.misses_addressed == 0
    assert report.edits == ()


def test_apply_optimization_only_applies_accepted_edits():
    """If we manually construct a report containing only rejected edits,
    apply_optimization must apply zero of them."""
    s = _seeded_society()
    # Snapshot original tag sets.
    original = {
        (n.name, sk.id): tuple(sk.tags)
        for n in s.graph.agents()
        for sk in n.card.skills
    }
    rejected_only = OptimizationReport(
        edits=(
            SkillEdit(
                agent="analyst-agent",
                skill_id="data_analysis",
                add_tags=("fakeword",),
                reason="constructed for test",
                fixes=0,
                regressions=2,
            ),
        ),
        labels_seen=1,
        labels_missing_explanations=0,
        labels_already_correct=0,
        misses_addressed=1,
    )
    applied = s.apply_optimization(rejected_only)
    assert applied == 0

    after = {
        (n.name, sk.id): tuple(sk.tags)
        for n in s.graph.agents()
        for sk in n.card.skills
    }
    assert original == after, "no edits should have been applied"


def test_report_renders_without_crashing():
    s = _seeded_society()
    labels = _run_suite(s)
    report = s.optimize(labels)
    text = report.render()
    assert "agent2society optimization report" in text
    assert "labels seen:" in text
    # cp1252-safe: should encode without exotic chars
    text.encode("cp1252")  # raises if any character is outside the codepage
