"""Property tests for the frozen assertion schema (invariants I1–I4).

If any test here needs changing, that is a SCHEMA CHANGE and requires an ADR.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from mnema.core.models import (
    Assertion,
    Confidence,
    EpistemicStatus,
    OriginType,
    Provenance,
)

# ------------------------------------------------------------- strategies ---
utc_dt = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2100, 1, 1),
    timezones=st.just(UTC),
)

provenances = st.builds(
    Provenance,
    origin_type=st.sampled_from(list(OriginType)),
    source_id=st.text(min_size=1, max_size=40),
    derived_from=st.lists(st.text(min_size=1, max_size=12), max_size=3).map(tuple),
    method=st.none() | st.text(min_size=1, max_size=30),
)

confidences = st.builds(
    Confidence,
    value=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    method=st.text(min_size=1, max_size=20),
)


@st.composite
def assertions(draw: st.DrawFn) -> Assertion:
    vf = draw(st.none() | utc_dt)
    vt = draw(st.none() | utc_dt)
    if vf and vt and vf > vt:
        vf, vt = vt, vf
    return Assertion(
        agent_id=draw(st.text(min_size=1, max_size=20)),
        namespace=draw(st.text(min_size=1, max_size=20)),
        statement=draw(st.text(min_size=1, max_size=200)),
        subject=draw(st.none() | st.text(min_size=1, max_size=40)),
        predicate=draw(st.none() | st.text(min_size=1, max_size=40)),
        object=draw(st.none() | st.text(min_size=1, max_size=40)),
        valid_from=vf,
        valid_to=vt,
        provenance=draw(provenances),
        confidence=draw(confidences),
        status=draw(st.sampled_from(list(EpistemicStatus))),
    )


# ------------------------------------------------------------------ tests ---
@settings(max_examples=200)
@given(assertions())
def test_json_roundtrip_is_lossless(a: Assertion) -> None:
    restored = Assertion.model_validate_json(a.model_dump_json())
    assert restored == a
    assert restored.content_hash == a.content_hash


@settings(max_examples=200)
@given(assertions())
def test_immutability_I1(a: Assertion) -> None:
    with pytest.raises(ValidationError):
        a.statement = "mutated"  # type: ignore[misc]


@settings(max_examples=200)
@given(assertions())
def test_content_hash_ignores_epistemics_I3(a: Assertion) -> None:
    """Same proposition, different confidence/status/id/asserted_at → same hash."""
    twin = Assertion(
        agent_id=a.agent_id,
        namespace=a.namespace,
        statement=a.statement,
        subject=a.subject,
        predicate=a.predicate,
        object=a.object,
        valid_from=a.valid_from,
        valid_to=a.valid_to,
        provenance=a.provenance,
        confidence=Confidence(value=0.123, method="other"),
        status=EpistemicStatus.HYPOTHESIS,
    )
    assert twin.id != a.id
    assert twin.content_hash == a.content_hash


@settings(max_examples=200)
@given(assertions(), st.text(min_size=1, max_size=200))
def test_content_hash_sensitive_to_statement(a: Assertion, other: str) -> None:
    if other == a.statement:
        return
    changed = Assertion(
        agent_id=a.agent_id,
        namespace=a.namespace,
        statement=other,
        provenance=a.provenance,
        confidence=a.confidence,
    )
    assert changed.content_hash != a.content_hash


@settings(max_examples=200)
@given(assertions())
def test_revision_chain_I1(a: Assertion) -> None:
    revised = a.revise(statement=a.statement + " (revised)")
    assert revised.id != a.id
    assert revised.supersedes == a.id
    assert revised.agent_id == a.agent_id
    # retraction is a revision, not a deletion
    dead = a.retract()
    assert dead.status is EpistemicStatus.RETRACTED
    assert dead.supersedes == a.id


def test_validity_window_ordering_rejected_I2() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Assertion(
            agent_id="a1",
            statement="x",
            valid_from=t0,
            valid_to=t0 - timedelta(days=1),
            provenance=Provenance(origin_type=OriginType.SYSTEM, source_id="test"),
            confidence=Confidence(value=1.0),
        )


def test_naive_datetimes_rejected_I2() -> None:
    with pytest.raises(ValidationError):
        Assertion(
            agent_id="a1",
            statement="x",
            valid_from=datetime(2026, 1, 1),  # naive
            provenance=Provenance(origin_type=OriginType.SYSTEM, source_id="test"),
            confidence=Confidence(value=1.0),
        )


def test_provenance_is_mandatory_I4() -> None:
    with pytest.raises(ValidationError):
        Assertion(agent_id="a1", statement="x", confidence=Confidence(value=0.5))  # type: ignore[call-arg]
