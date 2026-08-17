"""Consolidation artifacts — FROZEN at v0.0 (see docs/adr/ADR-0002).

Outputs of the sleep-phase consolidator (S2.2). Mirrors `Assertion`'s
discipline:

  I1  Immutable. Revision = new instance + `superseded_by` on the old one
      (set atomically during the next digest's save; the model itself
      never mutates in place).
  I3  Content-addressed: `content_hash` covers propositional content only.
  I4  Every artifact carries `derived_from` (assertion ids). Non-empty.
  I5  Imports pydantic + stdlib only. `ast` for Python-code validation is
      stdlib; no third-party parser.

The `Digest` is the manifest an agent's "consolidated" arm consumes at
retrieval time — see `Digest.render()`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "0.0"

_SNAKE_CASE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_rule_id() -> str:
    return f"rul_{uuid.uuid4().hex}"


def _new_skill_id() -> str:
    return f"skl_{uuid.uuid4().hex}"


# ------------------------------------------------------------------- Rule ----
class Rule(BaseModel):
    """A one-sentence imperative distilled from ≥1 assertion.

    Example: "Use `client.fetch_v2()`; `fetch()` was removed in lib v2."
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=_new_rule_id)
    schema_version: str = Field(default=SCHEMA_VERSION)
    text: str = Field(min_length=1, description="Imperative, one sentence.")
    scope: str = Field(min_length=1, description="Subject/topic this rule applies to.")
    derived_from: tuple[str, ...] = Field(
        min_length=1, description="Assertion ids this rule was distilled from (I4)."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    digest_version: int = Field(ge=1, description="Digest version that introduced this rule.")
    created_at: datetime = Field(default_factory=_utcnow)
    superseded_by: str | None = Field(
        default=None, description="Id of the rule that supersedes this one (set at next digest)."
    )

    @field_validator("created_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v

    @field_validator("derived_from")
    @classmethod
    def _derived_ids_nonempty(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if any(not s for s in v):
            raise ValueError("derived_from ids must be non-empty strings")
        return v

    @property
    def content_hash(self) -> str:
        """sha256 over propositional content ONLY (I3)."""
        canon = json.dumps(
            {
                "schema_version": self.schema_version,
                "text": self.text,
                "scope": self.scope,
                "derived_from": list(self.derived_from),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ Skill ----
class Skill(BaseModel):
    """A self-contained Python function distilled from success trajectories.

    `code` is validated at construction: it must parse and define a
    top-level function whose name matches `name`.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=_new_skill_id)
    schema_version: str = Field(default=SCHEMA_VERSION)
    name: str = Field(min_length=1, description="snake_case function name.")
    description: str = Field(min_length=1)
    code: str = Field(min_length=1, description="Complete Python function as text.")
    signature: str = Field(
        min_length=1, description='e.g. "def parse_manifest(path: str) -> dict"'
    )
    derived_from: tuple[str, ...] = Field(
        min_length=1, description="Assertion ids this skill was distilled from (I4)."
    )
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    digest_version: int = Field(ge=1)
    created_at: datetime = Field(default_factory=_utcnow)
    superseded_by: str | None = Field(default=None)

    @field_validator("created_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v

    @field_validator("name")
    @classmethod
    def _snake_case(cls, v: str) -> str:
        if not _SNAKE_CASE_RE.match(v):
            raise ValueError(f"skill name must be snake_case, got {v!r}")
        return v

    @field_validator("derived_from")
    @classmethod
    def _derived_ids_nonempty(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if any(not s for s in v):
            raise ValueError("derived_from ids must be non-empty strings")
        return v

    # Modules that must not appear in skill code — defense-in-depth against
    # LLM-generated code that could execute shell commands or exfiltrate data.
    _BLOCKED_IMPORTS: frozenset[str] = frozenset({
        "os", "subprocess", "sys", "socket", "shutil", "pty",
        "ctypes", "cffi", "pickle", "shelve", "marshal",
        "importlib", "builtins", "__builtins__",
    })

    @model_validator(mode="after")
    def _code_parses_and_defines_name(self) -> Skill:
        try:
            tree = ast.parse(self.code)
        except SyntaxError as e:
            raise ValueError(f"skill code does not parse: {e}") from e
        top_fns = {
            n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if self.name not in top_fns:
            raise ValueError(
                f"skill code must define a top-level function named {self.name!r}; "
                f"found {sorted(top_fns) or 'none'}"
            )
        # Walk AST and block dangerous imports.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in self._BLOCKED_IMPORTS:
                        raise ValueError(
                            f"skill code imports blocked module {alias.name!r}; "
                            "use a safe library or request a whitelist exception"
                        )
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in self._BLOCKED_IMPORTS:
                    raise ValueError(
                        f"skill code imports blocked module {node.module!r}; "
                        "use a safe library or request a whitelist exception"
                    )
        return self

    @property
    def content_hash(self) -> str:
        """sha256 over propositional content ONLY (I3). Counts are NOT hashed."""
        canon = json.dumps(
            {
                "schema_version": self.schema_version,
                "name": self.name,
                "signature": self.signature,
                "code": self.code,
                "derived_from": list(self.derived_from),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- Digest ----
class Digest(BaseModel):
    """A consolidation snapshot: active rules + skills for a namespace/agent.

    Persisted by the SQLite adapter as a manifest row plus rows in the rules
    and skills tables. When loaded, `rules` and `skills` are the ones ACTIVE
    at this digest's version (not superseded by anything at or before it).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    version: int = Field(ge=1)
    namespace: str = Field(default="default", min_length=1)
    agent_id: str = Field(min_length=1)
    rules: tuple[Rule, ...] = Field(default=())
    skills: tuple[Skill, ...] = Field(default=())
    source_event_seq_range: tuple[int, int] = Field(
        description="Half-open WAL range this digest consolidated: [start, end]."
    )
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("created_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v

    @field_validator("source_event_seq_range")
    @classmethod
    def _range_ordered(cls, v: tuple[int, int]) -> tuple[int, int]:
        start, end = v
        if start < 0 or end < 0:
            raise ValueError("source_event_seq_range values must be >= 0")
        if end < start:
            raise ValueError("source_event_seq_range end must be >= start")
        return v

    # ------------------------------------------------------------ manifest --
    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.rules)

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.skills)

    # ------------------------------------------------------------- render ---
    def render(self, max_chars: int = 4000) -> str:
        """Deterministic markdown for prompt injection.

        Rules ordered by (confidence desc, created_at asc, id asc).
        Skill signatures ordered by (signature asc, id asc).
        Overflow drops entire lowest-confidence rules last-first; skills are
        dropped only after all rules are gone. Never truncates mid-line.

        Same digest → byte-identical output across processes.
        """
        if max_chars < 0:
            raise ValueError("max_chars must be >= 0")

        header = f"# Consolidated memory (digest v{self.version})\n"

        rules_sorted = sorted(
            self.rules,
            key=lambda r: (-r.confidence, r.created_at, r.id),
        )
        skills_sorted = sorted(
            self.skills,
            key=lambda s: (s.signature, s.id),
        )

        def rule_line(r: Rule) -> str:
            # One rule per line, escaped of embedded newlines for determinism.
            text = r.text.replace("\n", " ").strip()
            return f"- ({r.confidence:.2f}) [{r.scope}] {text}"

        def skill_line(s: Skill) -> str:
            return f"- {s.signature}"

        rule_lines = [rule_line(r) for r in rules_sorted]
        skill_lines = [skill_line(s) for s in skills_sorted]

        # Assemble the *maximal* rendering; then trim last-first.
        def assemble(rules_kept: list[str], skills_kept: list[str]) -> str:
            parts = [header]
            if rules_kept:
                parts.append("\n## Rules\n" + "\n".join(rules_kept) + "\n")
            if skills_kept:
                parts.append("\n## Skills\n" + "\n".join(skills_kept) + "\n")
            return "".join(parts)

        full = assemble(rule_lines, skill_lines)
        if len(full) <= max_chars:
            return full

        # Drop skills last (they're the smallest/cheapest signal to lose last),
        # rules first (lowest-confidence == end of the sorted list).
        # Correction: spec says rules dropped last-first (lowest-confidence
        # first), and skills only after rules are gone. Skills-drop policy:
        # drop them last-first too (last in signature order).
        kept_rules = list(rule_lines)
        kept_skills = list(skill_lines)

        while len(assemble(kept_rules, kept_skills)) > max_chars and kept_rules:
            kept_rules.pop()  # drop the last (lowest-confidence) rule
        while len(assemble(kept_rules, kept_skills)) > max_chars and kept_skills:
            kept_skills.pop()

        return assemble(kept_rules, kept_skills)

    def model_dump_stable(self) -> dict[str, Any]:
        """A dict form guaranteed stable for hashing/serialization."""
        return json.loads(self.model_dump_json())
