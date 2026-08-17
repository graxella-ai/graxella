"""Regex extractors — turn markdown into typed Assertions.

Five patterns, no NLP, no LLM. Each returns Assertions with a source_uri
citing the file + line so an auditor can trace every triple back to its
originating sentence.

Extracted patterns:

  * deprecation notice   -> deprecated_by
        "`get_weather` is deprecated. Use `fetch_forecast` instead."
        "**Deprecated.** Use `fetch_forecast`."   (subject = nearest ### header)

  * migration table      -> deprecated_by
        | Old | New |
        | get_weather | fetch_forecast |

  * field rename         -> field_maps_to
        "Renamed `city` -> `location` in v2."
        "The `city` field is now `location`."

  * intent binding       -> has_intent
        "Intent: weather_lookup"   (under a ### tool_name header)

  * signature / args     -> has_arg + described_as
        "### fetch_forecast(location: str) -> str"
        "Fetches current weather forecast for a location."

The design choice: prefer high-precision patterns over high-recall ones.
A miner backed by these extractors emits proposals that will be reviewed
by a human, so a missed extraction costs a proposal; a false extraction
costs reviewer trust — the second is more expensive.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

from graxella.knowledge.seed import Assertion


# --------------------------------------------------------------- primitives
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_BT_IDENT = rf"`({_IDENT})`"                # `identifier`
_ARROW = r"(?:->|->|=>|:)"                  # arrow-ish separators used in prose

# Words that introduce the successor tool. Broadened beyond "use" so common
# migration prose ("superseded by X", "replaced by X") is caught too.
_SUCCESSOR_VERB = r"(?:use|superseded\s+by|replaced\s+by|migrate\s+to)"

# `old` [some words] deprecated [some words] use `new`
# Allow up to a short prose gap before 'deprecated' ("tool is deprecated",
# "endpoint has been deprecated", ...) but disallow crossing a newline OR
# another backtick identifier (that would confuse the subject).
_RE_DEPR_INLINE = re.compile(
    rf"{_BT_IDENT}[^`\n]{{0,60}}?deprecated[^\n]*?\b{_SUCCESSOR_VERB}\s+{_BT_IDENT}",
    re.IGNORECASE,
)

# **Deprecated.** ... use `new`  (subject comes from the nearest header above)
_RE_DEPR_HEADER = re.compile(
    rf"\*\*Deprecated\.\*\*[^.\n]*?\b{_SUCCESSOR_VERB}\s+{_BT_IDENT}",
    re.IGNORECASE,
)

# Renamed `old` -> `new`  |  Renamed `old` to `new`  |  `old` field is now `new`
_RE_RENAME = re.compile(
    rf"(?:renamed\s+){_BT_IDENT}\s*(?:{_ARROW}|to)\s*{_BT_IDENT}",
    re.IGNORECASE,
)
_RE_FIELD_NOW = re.compile(
    rf"{_BT_IDENT}\s+field\s+is\s+now\s+{_BT_IDENT}",
    re.IGNORECASE,
)

# ### tool_name  (any ATX header line)
_RE_HEADER = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
# Extract identifier from a header like "### fetch_forecast(location: str) -> str"
_RE_HEADER_IDENT = re.compile(rf"^\s*(?:#+\s+)?({_IDENT})\s*(?:\(|$)")

# Intent: weather_lookup   (case-insensitive, colon required)
_RE_INTENT = re.compile(rf"^\s*intent\s*:\s*({_IDENT})\s*$", re.IGNORECASE)

# Signature: name(arg1: T, arg2: T) -> T  (works inside a header or a code block)
_RE_SIGNATURE = re.compile(
    rf"({_IDENT})\s*\(\s*([^)]*)\s*\)"
)

# Markdown pipe-table row: |  a  |  b  |
_RE_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


# --------------------------------------------------------------- data class
@dataclass
class _HeaderCtx:
    """Nearest ATX header above the current line — anchors header-relative facts."""
    ident: str = ""
    raw: str = ""
    line_no: int = 0


def _mk_uri(source: str, line_no: int) -> str:
    return f"file:///{source}#L{line_no}"


def _header_ident(header_text: str) -> str:
    m = _RE_HEADER_IDENT.match(header_text)
    return m.group(1) if m else ""


# --------------------------------------------------------------- extractors
def _extract_deprecations(text: str, source: str) -> Iterator[Assertion]:
    header = _HeaderCtx()
    for i, line in enumerate(text.splitlines(), start=1):
        h = _RE_HEADER.match(line)
        if h:
            header = _HeaderCtx(ident=_header_ident(h.group(1)),
                                raw=h.group(1), line_no=i)
            continue

        for m in _RE_DEPR_INLINE.finditer(line):
            old, new = m.group(1), m.group(2)
            yield Assertion(
                subject=old, predicate="deprecated_by", object=new,
                source_uri=_mk_uri(source, i),
                evidence=line.strip(),
                confidence=0.9,
            )

        if header.ident:
            for m in _RE_DEPR_HEADER.finditer(line):
                new = m.group(1)
                if new == header.ident:
                    continue
                yield Assertion(
                    subject=header.ident, predicate="deprecated_by", object=new,
                    source_uri=_mk_uri(source, i),
                    evidence=line.strip(),
                    confidence=0.85,  # header-relative — slightly less certain
                )


def _extract_field_renames(text: str, source: str) -> Iterator[Assertion]:
    """Emit field_maps_to using the OLD_TOOL.field convention. Requires a
    nearby '### old_tool' header AND a companion 'Migrates from `X`' hint,
    OR a preceding deprecation on the same header block.

    Fallback: if no tool anchor is available, emit ungrounded field renames
    with subject/object as bare field names (won't feed field_map_for lookups
    but still visible in `stats()`).
    """
    header = _HeaderCtx()
    # anchor_new: the tool the current section documents (i.e., the header ident)
    # anchor_old: the tool that anchor_new deprecates (from a nearby depr line)
    anchor_old: str = ""

    for i, line in enumerate(text.splitlines(), start=1):
        h = _RE_HEADER.match(line)
        if h:
            header = _HeaderCtx(ident=_header_ident(h.group(1)),
                                raw=h.group(1), line_no=i)
            anchor_old = ""
            continue

        # Cheap way to learn what OLD this NEW section replaces:
        # "Migrates from `get_weather`."
        mig = re.search(rf"migrates?\s+from\s+{_BT_IDENT}", line, re.IGNORECASE)
        if mig and header.ident:
            anchor_old = mig.group(1)

        candidates: list[tuple[str, str, str]] = []
        for m in _RE_RENAME.finditer(line):
            candidates.append((m.group(1), m.group(2), line.strip()))
        for m in _RE_FIELD_NOW.finditer(line):
            candidates.append((m.group(1), m.group(2), line.strip()))

        for old_field, new_field, evidence in candidates:
            if anchor_old and header.ident:
                subj = f"{anchor_old}.{old_field}"
                obj = f"{header.ident}.{new_field}"
            else:
                subj, obj = old_field, new_field
            yield Assertion(
                subject=subj, predicate="field_maps_to", object=obj,
                source_uri=_mk_uri(source, i),
                evidence=evidence,
                confidence=0.9 if anchor_old else 0.5,
            )


def _extract_intents_and_signatures(text: str, source: str) -> Iterator[Assertion]:
    header = _HeaderCtx()
    desc_taken = False  # per-header: emit at most one described_as
    for i, line in enumerate(text.splitlines(), start=1):
        h = _RE_HEADER.match(line)
        if h:
            ident = _header_ident(h.group(1))
            header = _HeaderCtx(ident=ident, raw=h.group(1), line_no=i)
            desc_taken = False
            if ident:
                sig = _RE_SIGNATURE.search(h.group(1))
                if sig and sig.group(1) == ident:
                    for arg in _parse_args(sig.group(2)):
                        yield Assertion(
                            subject=ident, predicate="has_arg", object=arg,
                            source_uri=_mk_uri(source, i),
                            evidence=h.group(1).strip(),
                            confidence=0.95,
                        )
            continue

        if not header.ident:
            continue

        m_intent = _RE_INTENT.match(line)
        if m_intent:
            yield Assertion(
                subject=header.ident, predicate="has_intent", object=m_intent.group(1),
                source_uri=_mk_uri(source, i),
                evidence=line.strip(),
                confidence=1.0,
            )
            continue

        # First non-empty prose line under a header becomes the description —
        # cheap and works well for the "### tool_name\n\nOne-line summary." shape.
        # Keep the header alive after: 'Intent:' / other facts may follow.
        if desc_taken:
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith(("|", ">", "*", "-", "`", "#")):
            if not any(True for _ in _RE_INTENT.finditer(stripped)):
                yield Assertion(
                    subject=header.ident, predicate="described_as", object=stripped,
                    source_uri=_mk_uri(source, i),
                    evidence=stripped,
                    confidence=0.7,
                )
                desc_taken = True


def _extract_migration_tables(text: str, source: str) -> Iterator[Assertion]:
    """A pipe-table with headers 'Old' and 'New' becomes rename assertions.

    Two classifications:
      * inside a tool-migration section (nearest header is a tool ident AND a
        prior line said 'migrates from `X`') → emit `field_maps_to` with
        subject/object namespaced as `old_tool.field` / `new_tool.field`.
      * otherwise → emit `deprecated_by` (tool-level rename).

    Deciding based on the surrounding section avoids the common mistake of
    reading a field-rename table as a tool-rename table.
    """
    lines = text.splitlines()
    header = _HeaderCtx()
    anchor_old: str = ""

    def _rescan_header_before(idx: int) -> tuple[_HeaderCtx, str]:
        hdr = _HeaderCtx()
        anchor = ""
        for k in range(idx - 1, -1, -1):
            hm = _RE_HEADER.match(lines[k])
            if hm:
                hdr = _HeaderCtx(ident=_header_ident(hm.group(1)),
                                 raw=hm.group(1), line_no=k + 1)
                break
        if hdr.ident:
            for k in range(hdr.line_no, idx):
                mig = re.search(rf"migrates?\s+from\s+{_BT_IDENT}",
                                lines[k], re.IGNORECASE)
                if mig:
                    anchor = mig.group(1)
                    break
        return hdr, anchor

    i = 0
    while i < len(lines) - 1:
        # Keep header + anchor_old current for the linear walk.
        h = _RE_HEADER.match(lines[i])
        if h:
            header = _HeaderCtx(ident=_header_ident(h.group(1)),
                                raw=h.group(1), line_no=i + 1)
            anchor_old = ""
            i += 1
            continue
        mig = re.search(rf"migrates?\s+from\s+{_BT_IDENT}", lines[i], re.IGNORECASE)
        if mig and header.ident:
            anchor_old = mig.group(1)

        m = _RE_TABLE_ROW.match(lines[i])
        if not m:
            i += 1
            continue
        headers = [c.strip().lower() for c in m.group(1).split("|")]
        sep = _RE_TABLE_ROW.match(lines[i + 1] or "")
        if not sep or not all(re.match(r":?-{2,}:?", c.strip())
                              for c in sep.group(1).split("|")):
            i += 1
            continue
        try:
            old_idx = headers.index("old")
            new_idx = headers.index("new")
        except ValueError:
            i += 2
            continue

        # If our linear walk missed the header/anchor (e.g. table is the
        # first content-bearing thing after a header we already skipped
        # past), rescan the region above the table.
        table_hdr, table_anchor = (header, anchor_old)
        if not (table_hdr.ident and table_anchor):
            table_hdr, table_anchor = _rescan_header_before(i)

        as_fields = bool(table_hdr.ident and table_anchor)

        j = i + 2
        while j < len(lines):
            rm = _RE_TABLE_ROW.match(lines[j])
            if not rm:
                break
            cells = [c.strip().strip("`") for c in rm.group(1).split("|")]
            if len(cells) > max(old_idx, new_idx):
                old, new = cells[old_idx], cells[new_idx]
                if _IDENT_RE.fullmatch(old) and _IDENT_RE.fullmatch(new):
                    if as_fields:
                        yield Assertion(
                            subject=f"{table_anchor}.{old}",
                            predicate="field_maps_to",
                            object=f"{table_hdr.ident}.{new}",
                            source_uri=_mk_uri(source, j + 1),
                            evidence=lines[j].strip(),
                            confidence=0.95,
                        )
                    else:
                        yield Assertion(
                            subject=old, predicate="deprecated_by", object=new,
                            source_uri=_mk_uri(source, j + 1),
                            evidence=lines[j].strip(),
                            confidence=0.95,
                        )
            j += 1
        i = j


_IDENT_RE = re.compile(_IDENT)


def _parse_args(args_str: str) -> Iterator[str]:
    """Very small signature parser — 'x: int, y: str = "a"' -> ['x', 'y']."""
    for chunk in args_str.split(","):
        chunk = chunk.strip()
        if not chunk or chunk in ("*", "/", "**"):
            continue
        name = chunk.split(":", 1)[0].split("=", 1)[0].strip()
        name = name.lstrip("*")
        if _IDENT_RE.fullmatch(name):
            yield name


# --------------------------------------------------------------- public
def extract_all(text: str, source: str) -> list[Assertion]:
    """Run every extractor over `text` and dedupe by (subject, predicate, object).

    Multiple extractors may produce the same fact (a table + an inline note).
    Keep the highest-confidence witness so downstream code sees the strongest
    citation.
    """
    seen: dict[tuple[str, str, str], Assertion] = {}
    for extractor in (
        _extract_deprecations,
        _extract_migration_tables,
        _extract_field_renames,
        _extract_intents_and_signatures,
    ):
        for a in extractor(text, source):
            k = (a.subject, a.predicate, a.object)
            prior = seen.get(k)
            if prior is None or a.confidence > prior.confidence:
                seen[k] = a
    return list(seen.values())
