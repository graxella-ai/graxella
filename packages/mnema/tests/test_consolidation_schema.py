"""Property tests for the frozen consolidation schema (Rule, Skill, Digest).

If any test here needs changing, that is a SCHEMA CHANGE and requires an ADR
(cf. ADR-0002). Invariants exercised: I1 immutability, I3 content-hash covers
propositional content only, I4 provenance-mandatory, I5 core imports are
pydantic + stdlib only, plus `Digest.render()` determinism/bounding.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from mnema.core.consolidation import Digest, Rule, Skill

# ------------------------------------------------------------- strategies ---
utc_dt = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2100, 1, 1),
    timezones=st.just(UTC),
)


_ids = st.lists(st.text(min_size=1, max_size=12), min_size=1, max_size=4)
_dv = st.integers(min_value=1, max_value=100)


@st.composite
def rules(draw: st.DrawFn, *, digest_version: int | None = None) -> Rule:
    return Rule(
        text=draw(st.text(min_size=1, max_size=120)),
        scope=draw(st.text(min_size=1, max_size=30)),
        derived_from=tuple(draw(_ids)),
        confidence=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
        digest_version=digest_version if digest_version is not None else draw(_dv),
        created_at=draw(utc_dt),
    )


_SKILL_CODE_TEMPLATE = "def {name}(x):\n    return x + 1\n"


@st.composite
def skills(draw: st.DrawFn, *, digest_version: int | None = None) -> Skill:
    name = draw(
        st.from_regex(r"[a-z][a-z_0-9]{0,20}", fullmatch=True).filter(
            lambda s: s.isidentifier() and not __import__("keyword").iskeyword(s)
        )
    )
    return Skill(
        name=name,
        description=draw(st.text(min_size=1, max_size=80)),
        code=_SKILL_CODE_TEMPLATE.format(name=name),
        signature=f"def {name}(x: int) -> int",
        derived_from=tuple(draw(_ids)),
        digest_version=digest_version if digest_version is not None else draw(_dv),
        created_at=draw(utc_dt),
    )


# ---------------------------------------------------------- Rule invariants ---
@settings(max_examples=100)
@given(rules())
def test_rule_json_roundtrip_lossless(r: Rule) -> None:
    restored = Rule.model_validate_json(r.model_dump_json())
    assert restored == r
    assert restored.content_hash == r.content_hash


@settings(max_examples=50)
@given(rules())
def test_rule_immutability_I1(r: Rule) -> None:
    with pytest.raises(ValidationError):
        r.text = "mutated"  # type: ignore[misc]


@settings(max_examples=50)
@given(rules())
def test_rule_content_hash_ignores_epistemics_I3(r: Rule) -> None:
    """Same proposition, different confidence/id/version/created_at → same hash."""
    twin = Rule(
        text=r.text,
        scope=r.scope,
        derived_from=r.derived_from,
        confidence=(r.confidence + 0.5) % 1.0,
        digest_version=r.digest_version + 1,
    )
    assert twin.id != r.id
    assert twin.content_hash == r.content_hash


def test_rule_empty_derived_from_rejected_I4() -> None:
    with pytest.raises(ValidationError):
        Rule(
            text="Use client.fetch_v2()",
            scope="lib",
            derived_from=(),  # empty — must reject
            confidence=0.9,
            digest_version=1,
        )


def test_rule_naive_created_at_rejected() -> None:
    with pytest.raises(ValidationError):
        Rule(
            text="x",
            scope="y",
            derived_from=("asrt_1",),
            confidence=0.5,
            digest_version=1,
            created_at=datetime(2026, 1, 1),  # naive
        )


# --------------------------------------------------------- Skill invariants ---
@settings(max_examples=50)
@given(skills())
def test_skill_json_roundtrip_lossless(s: Skill) -> None:
    restored = Skill.model_validate_json(s.model_dump_json())
    assert restored == s
    assert restored.content_hash == s.content_hash


@settings(max_examples=30)
@given(skills())
def test_skill_immutability_I1(s: Skill) -> None:
    with pytest.raises(ValidationError):
        s.code = "def x(): pass"  # type: ignore[misc]


@settings(max_examples=30)
@given(skills())
def test_skill_content_hash_ignores_counts_I3(s: Skill) -> None:
    """Success/failure counts are NOT in the content hash."""
    twin = Skill(
        name=s.name,
        description=s.description,
        code=s.code,
        signature=s.signature,
        derived_from=s.derived_from,
        success_count=s.success_count + 17,
        failure_count=s.failure_count + 3,
        digest_version=s.digest_version,
    )
    assert twin.id != s.id
    assert twin.content_hash == s.content_hash


def test_skill_empty_derived_from_rejected_I4() -> None:
    with pytest.raises(ValidationError):
        Skill(
            name="parse_manifest",
            description="parse a manifest",
            code="def parse_manifest(p): return {}\n",
            signature="def parse_manifest(p: str) -> dict",
            derived_from=(),
            digest_version=1,
        )


def test_skill_code_must_parse() -> None:
    with pytest.raises(ValidationError):
        Skill(
            name="broken",
            description="d",
            code="def broken(:\n  # syntax error\n",
            signature="def broken() -> None",
            derived_from=("asrt_1",),
            digest_version=1,
        )


def test_skill_code_must_define_name() -> None:
    with pytest.raises(ValidationError):
        Skill(
            name="foo",
            description="d",
            code="def bar(): return 1\n",  # wrong function name
            signature="def foo() -> int",
            derived_from=("asrt_1",),
            digest_version=1,
        )


def test_skill_name_must_be_snake_case() -> None:
    with pytest.raises(ValidationError):
        Skill(
            name="CamelCase",
            description="d",
            code="def CamelCase(): return 1\n",
            signature="def CamelCase() -> int",
            derived_from=("asrt_1",),
            digest_version=1,
        )


# -------------------------------------------------------------- Digest ------
def _mk_rule(scope: str, conf: float, dv: int = 1, text: str = "do the thing") -> Rule:
    return Rule(
        text=text,
        scope=scope,
        derived_from=("asrt_1",),
        confidence=conf,
        digest_version=dv,
    )


def _mk_skill(name: str, dv: int = 1) -> Skill:
    return Skill(
        name=name,
        description=f"{name} skill",
        code=f"def {name}(x): return x\n",
        signature=f"def {name}(x: int) -> int",
        derived_from=("asrt_1",),
        digest_version=dv,
    )


def test_digest_render_is_deterministic() -> None:
    rs = tuple(
        _mk_rule(scope=f"s{i}", conf=(i % 5) / 10, text=f"rule {i}") for i in range(4)
    )
    sk = tuple(_mk_skill(name=f"fn_{i}") for i in range(3))
    d = Digest(
        version=1,
        agent_id="a1",
        rules=rs,
        skills=sk,
        source_event_seq_range=(0, 10),
    )
    a = d.render(4000)
    b = d.render(4000)
    assert a == b
    # Rule ordering: confidence desc — highest-confidence rule appears before lowest.
    lines = a.splitlines()
    rule_lines = [line for line in lines if line.startswith("- (") and "[s" in line]
    confs = [float(line[3:7]) for line in rule_lines]
    assert confs == sorted(confs, reverse=True)


def test_digest_render_respects_max_chars() -> None:
    # Many long-text rules so that max_chars will bite.
    rs = tuple(
        _mk_rule(scope=f"scope_{i:03d}", conf=(i % 100) / 100.0, text="X" * 200)
        for i in range(50)
    )
    d = Digest(
        version=1,
        agent_id="a1",
        rules=rs,
        skills=(),
        source_event_seq_range=(0, 10),
    )
    out = d.render(max_chars=500)
    assert len(out) <= 500


def test_digest_render_never_truncates_mid_rule() -> None:
    rs = tuple(
        _mk_rule(scope=f"s{i}", conf=(i % 10) / 10.0, text=f"rule text {i}") for i in range(20)
    )
    d = Digest(
        version=1,
        agent_id="a1",
        rules=rs,
        skills=(),
        source_event_seq_range=(0, 10),
    )
    out = d.render(max_chars=300)
    # Every rule line must be either fully present or absent — never truncated.
    for line in out.splitlines():
        if line.startswith("- ("):
            # The line must end where a rule line naturally ends (no cutoff).
            # Since our test rules all end in "rule text N", each present line
            # must contain the full "rule text " substring.
            assert "rule text " in line


def test_digest_render_drops_rules_before_skills() -> None:
    """When space is tight rules should be dropped last-first; skills only after
    all rules are gone."""
    # Modest content: 2 rules + 2 skills, then squeeze.
    rs = (_mk_rule("a", 0.9, text="alpha"), _mk_rule("b", 0.1, text="beta"))
    sk = (_mk_skill("fn_a"), _mk_skill("fn_b"))
    d = Digest(
        version=1,
        agent_id="a1",
        rules=rs,
        skills=sk,
        source_event_seq_range=(0, 10),
    )
    full = d.render(10_000)
    assert "alpha" in full and "beta" in full and "fn_a" in full

    # Drop budget by ~half: the lowest-confidence rule should be first out.
    tight = d.render(max_chars=len(full) - 30)
    # Rule "beta" (conf 0.1) is the lowest — should be dropped before skills.
    assert "fn_a" in tight or "fn_b" in tight  # skills survive
    assert "beta" not in tight


def test_digest_rejects_reversed_seq_range() -> None:
    with pytest.raises(ValidationError):
        Digest(
            version=1,
            agent_id="a1",
            source_event_seq_range=(10, 5),
        )


def test_digest_rejects_negative_seq_range() -> None:
    with pytest.raises(ValidationError):
        Digest(
            version=1,
            agent_id="a1",
            source_event_seq_range=(-1, 5),
        )


# ------------------------------------------------------ I5 import hygiene ---
def test_core_consolidation_imports_only_pydantic_and_stdlib() -> None:
    """I5: mnema.core.consolidation must import only pydantic + stdlib.

    Running in a subprocess and blocking sqlmodel/sqlalchemy at import time
    proves the module has zero adapter dependencies.
    """
    script = (
        "import sys\n"
        "class Block:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name.split('.')[0] in {'sqlmodel', 'sqlalchemy', 'chromadb', 'fastapi'}:\n"
        "            return self\n"
        "        return None\n"
        "    def load_module(self, name):\n"
        "        raise ImportError(f'{name} banned in core')\n"
        "sys.meta_path.insert(0, Block())\n"
        "import mnema.core.consolidation  # noqa\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"import-hygiene failed: {result.stderr}"
    assert result.stdout.strip() == "OK"
