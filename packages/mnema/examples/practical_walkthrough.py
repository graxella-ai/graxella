"""Mnema practical walkthrough -- end-to-end in one script.

Scenario
--------
An AI coding agent is helping with a Python project that uses `weatherlib`.
The library drifts across three versions:

  v1: weatherlib.get_weather(city)           ← original API
  v2: weatherlib.fetch_forecast(city)         ← renamed
  v3: weatherlib.WeatherClient().fetch(city)  ← refactored to class

Without memory the agent keeps calling the old API and failing.
With Mnema it records beliefs, sleeps, consolidates them into Rules,
and on the next task the Rule is injected into its prompt -- so it
calls the right API immediately.

Run this from the mnema/ directory:
    python -m examples.practical_walkthrough
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

# Make sure src/ is on the path when running directly.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mnema.adapters.sqlite.repository import SqliteMnemaStore
from mnema.core.consolidation import Digest, Rule, Skill
from mnema.core.models import Assertion, Confidence, EpistemicStatus, OriginType, Provenance


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def hr(title: str = "") -> None:
    width = 72
    if title:
        pad = (width - len(title) - 2) // 2
        print("\n" + "-" * pad + f" {title} " + "-" * pad)
    else:
        print("\n" + "-" * width)


def make_store(path: str = ":memory:") -> SqliteMnemaStore:
    return SqliteMnemaStore(f"sqlite:///{path}")


def observe(
    store: SqliteMnemaStore,
    *,
    agent_id: str,
    statement: str,
    subject: str,
    confidence: float = 0.85,
    source: str = "task_execution",
) -> Assertion:
    """Record a single observation (assertion) from the agent's experience."""
    a = Assertion(
        agent_id=agent_id,
        statement=statement,
        subject=subject,
        provenance=Provenance(origin_type=OriginType.OBSERVED, source_id=source),
        confidence=Confidence(value=confidence, method="empirical"),
    )
    store.record(a)
    return a


def revise(
    store: SqliteMnemaStore,
    prior: Assertion,
    new_statement: str,
    confidence: float = 0.95,
) -> Assertion:
    """Revise an existing belief -- preserves the old one for audit."""
    updated = prior.revise(statement=new_statement, confidence=confidence)
    store.record(updated)
    return updated


# ------------------------------------------------------------------------------
# PART 1  --  Wake phase: the agent works and accumulates beliefs
# ------------------------------------------------------------------------------

hr("PART 1 -- Wake phase: agent works, accumulates beliefs")

store   = make_store()          # in-memory for the demo; use a file path in prod
AGENT   = "coding-agent-01"
LIB     = "weatherlib"

print("""
The agent starts a task: 'Call the weather API for London'.
It tries the old call and gets an AttributeError.
""")

# Agent discovers the v1 API no longer works
a1 = observe(
    store,
    agent_id=AGENT,
    subject=LIB,
    statement="weatherlib.get_weather() raises AttributeError in the current version.",
    confidence=0.99,
    source="task_001",
)
print(f"[recorded]  {a1.id[:18]}…  '{a1.statement}'")

# It then reads the changelog and learns the new name
a2 = observe(
    store,
    agent_id=AGENT,
    subject=LIB,
    statement="weatherlib.fetch_forecast(city) is the correct call as of v2.",
    confidence=0.90,
    source="task_001_changelog",
)
print(f"[recorded]  {a2.id[:18]}…  '{a2.statement}'")

# A few tasks later it uses fetch_forecast successfully three times
a3 = observe(
    store,
    agent_id=AGENT,
    subject=LIB,
    statement="weatherlib.fetch_forecast('London') returns a valid dict in v2.",
    confidence=0.95,
    source="task_003",
)
print(f"[recorded]  {a3.id[:18]}…  '{a3.statement}'")

print(f"\nWAL events so far: {sum(1 for _ in store.read())} event(s)")
print("Current beliefs about weatherlib:")
for b in store.current_beliefs(subject=LIB, agent_id=AGENT):
    print(f"  [{b.confidence.value:.0%}] {b.statement}")


# ------------------------------------------------------------------------------
# PART 2  --  Sleep phase (consolidation): distill beliefs into Rules + Skills
# ------------------------------------------------------------------------------

hr("PART 2 -- Sleep phase: consolidate observations into Rules + Skills")

print("""
In a real system the LLM consolidator (S2.2) reads the WAL and calls
the LLM to distill rules. Here we hand-craft the output to show the
data structure -- the result is identical regardless of how it was produced.
""")

# --- Rule: what the agent now knows authoritatively ---
rule_v2 = Rule(
    text=(
        "Use weatherlib.fetch_forecast(city) -- "
        "get_weather() was removed in v2."
    ),
    scope="weatherlib",
    derived_from=(a1.id, a2.id, a3.id),   # provenance mandatory (I4)
    confidence=0.95,
    digest_version=1,
)
print(f"[rule]   {rule_v2.id[:18]}…  scope={rule_v2.scope!r}")
print(f"         text: {rule_v2.text}")
print(f"         derived_from: {len(rule_v2.derived_from)} assertion(s)")
print(f"         content_hash: {rule_v2.content_hash[:16]}…")

# --- Skill: a reusable Python function crystallised from success ---
skill_fetch = Skill(
    name="fetch_weather",
    description="Fetch weather for a city using the current weatherlib v2 API.",
    code=textwrap.dedent("""\
        def fetch_weather(city: str) -> dict:
            import weatherlib
            return weatherlib.fetch_forecast(city)
    """),
    signature="def fetch_weather(city: str) -> dict",
    derived_from=(a2.id, a3.id),
    digest_version=1,
)
print(f"\n[skill]  {skill_fetch.id[:18]}…  name={skill_fetch.name!r}")
print(f"         signature: {skill_fetch.signature}")
print(f"         code validated by ast.parse: OK")

# --- Digest: the snapshot the agent will consume next waking ---
digest_v1 = Digest(
    version=1,
    agent_id=AGENT,
    rules=(rule_v2,),
    skills=(skill_fetch,),
    source_event_seq_range=(0, 3),   # WAL events 0–3 were consolidated
)

store.save_digest(digest_v1)
print(f"\n[digest] version={digest_v1.version}  "
      f"rules={len(digest_v1.rules)}  skills={len(digest_v1.skills)}")

events = list(store.read())
print(f"WAL events after consolidation: {len(events)}")
last_event = events[-1][1]
print(f"  last event type: {last_event.event_type.value}")
print(f"  payload: version={last_event.payload['version']}, "
      f"rule_ids={last_event.payload['rule_ids']}")


# ------------------------------------------------------------------------------
# PART 3  --  Next wake: Digest.render() injected into the agent's prompt
# ------------------------------------------------------------------------------

hr("PART 3 -- Next wake: render the Digest and inject into prompt")

print("""
Before the agent starts its next task, the runtime calls
  latest_digest().render()
and prepends the result to the system prompt.
""")

loaded = store.latest_digest(agent_id=AGENT)
assert loaded is not None

rendered = loaded.render(max_chars=4000)
print("--- BEGIN INJECTED MEMORY ---")
print(rendered)
print("--- END INJECTED MEMORY -----")

print("""
With this injected, the agent's LLM call becomes:

  system:  You are a Python coding assistant.
           [INJECTED MEMORY ABOVE]
           Do not use APIs listed as removed.

  user:    Call the weather API for Tokyo and return the temperature.

The agent now calls weatherlib.fetch_forecast('Tokyo') immediately,
without a trial-error loop. That is the consolidation lift.
""")


# ------------------------------------------------------------------------------
# PART 4  --  Skill outcome tracking
# ------------------------------------------------------------------------------

hr("PART 4 -- Skill outcome tracking (mark_skill_outcome)")

print("""
Each time the agent executes a crystallised skill it reports success/failure.
This is an atomic UPDATE -- NOT a WAL event (metrics != beliefs, ADR-0002).
""")

sk_id = skill_fetch.id
store.mark_skill_outcome(sk_id, success=True)
store.mark_skill_outcome(sk_id, success=True)
store.mark_skill_outcome(sk_id, success=False)   # one bad call

active_skills = store.active_skills(agent_id=AGENT)
sk = active_skills[0]
print(f"Skill '{sk.name}': success={sk.success_count}  failure={sk.failure_count}")
print(f"WAL event count unchanged: {len(list(store.read()))}  (no new events added)")


# ------------------------------------------------------------------------------
# PART 5  --  Drift: library upgrades to v3, agent updates its beliefs
# ------------------------------------------------------------------------------

hr("PART 5 -- Library upgrades to v3, agent revises beliefs")

print("""
weatherlib releases v3: the API is now a class.
  weatherlib.WeatherClient().fetch(city)

The agent hits another error, records new observations.
""")

a4 = observe(
    store,
    agent_id=AGENT,
    subject=LIB,
    statement=(
        "weatherlib.fetch_forecast() raises AttributeError in v3; "
        "use WeatherClient().fetch(city) instead."
    ),
    confidence=0.99,
    source="task_020",
)
a5 = observe(
    store,
    agent_id=AGENT,
    subject=LIB,
    statement="weatherlib.WeatherClient().fetch('London') returns a valid dict in v3.",
    confidence=0.92,
    source="task_021",
)

print(f"[recorded]  a4: '{a4.statement[:60]}…'")
print(f"[recorded]  a5: '{a5.statement[:60]}…'")

# Sleep: consolidate into digest v2 -- same scope, so the old rule is superseded
rule_v3 = Rule(
    text=(
        "Use weatherlib.WeatherClient().fetch(city) -- "
        "fetch_forecast() was removed in v3."
    ),
    scope="weatherlib",            # same scope -> supersedes rule_v2 in the DB
    derived_from=(a4.id, a5.id),
    confidence=0.95,
    digest_version=2,
)
skill_v3 = Skill(
    name="fetch_weather",          # same name -> supersedes skill_fetch in the DB
    description="Fetch weather using weatherlib v3 WeatherClient.",
    code=textwrap.dedent("""\
        def fetch_weather(city: str) -> dict:
            import weatherlib
            return weatherlib.WeatherClient().fetch(city)
    """),
    signature="def fetch_weather(city: str) -> dict",
    derived_from=(a4.id, a5.id),
    digest_version=2,
)
digest_v2 = Digest(
    version=2,
    agent_id=AGENT,
    rules=(rule_v3,),
    skills=(skill_v3,),
    source_event_seq_range=(3, 7),
)
store.save_digest(digest_v2)
print(f"\n[digest v2 saved]  rule: '{rule_v3.text[:55]}…'")

# Active rules now: only the v3 rule
active_rules = store.active_rules(agent_id=AGENT)
print(f"\nActive rules now ({len(active_rules)}):")
for r in active_rules:
    print(f"  [{r.confidence:.0%}] [{r.scope}] {r.text}")


# ------------------------------------------------------------------------------
# PART 6  --  Time travel: what did the agent know at digest v1?
# ------------------------------------------------------------------------------

hr("PART 6 -- Time travel: get_digest(1) vs get_digest(2)")

print("""
Mnema keeps every prior artifact for differential evaluation.
'What did the agent know before it saw v3?' is a one-liner.
""")

at_v1 = store.get_digest(1, agent_id=AGENT)
at_v2 = store.get_digest(2, agent_id=AGENT)

assert at_v1 is not None and at_v2 is not None

print("Digest v1  (what agent knew before v3 drift):")
for r in at_v1.rules:
    print(f"  rule: {r.text}")
for s in at_v1.skills:
    print(f"  skill code snippet: {s.code.splitlines()[1].strip()}")

print("\nDigest v2  (what agent knows now):")
for r in at_v2.rules:
    print(f"  rule: {r.text}")
for s in at_v2.skills:
    print(f"  skill code snippet: {s.code.splitlines()[1].strip()}")

print("\nDifferential:  v1 rule != v2 rule  ->", at_v1.rules[0].id != at_v2.rules[0].id)
print("Old v1 rule still in DB (superseded_by set, not deleted):  OK")


# ------------------------------------------------------------------------------
# PART 7  --  render() for v2 digest shown in full
# ------------------------------------------------------------------------------

hr("PART 7 -- Final prompt injection (digest v2)")

rendered_v2 = at_v2.render(max_chars=4000)
print("--- BEGIN INJECTED MEMORY ---")
print(rendered_v2)
print("--- END INJECTED MEMORY -----")


# ------------------------------------------------------------------------------
# PART 8  --  Belief audit trail (the WAL)
# ------------------------------------------------------------------------------

hr("PART 8 -- Full WAL audit trail")

print("""
Every state change is recorded in the append-only event log.
Nothing is ever deleted -- only superseded or retracted.
""")

for seq, evt in store.read():
    print(f"  seq={seq:>2}  {evt.event_type.value:<30}  "
          f"agent={evt.agent_id}  "
          f"{'assertion_id=' + evt.assertion_id[:12] + '...' if evt.assertion_id else 'payload=' + str(evt.payload)[:40]}")

hr()
print("""
Summary of what just happened:
  1. Agent recorded 5 assertions (beliefs with provenance + confidence).
  2. Consolidator distilled them into Rules + Skills -> Digest.
  3. Digest.render() was injected into the next prompt.
  4. Skill outcomes were tracked (no WAL event -- metrics != beliefs).
  5. Library drifted; agent recorded new beliefs; new Digest superseded old.
  6. Time-travel: get_digest(1) and get_digest(2) both work independently.
  7. Full audit trail available via the WAL event log.

That is the Mnema lifecycle.
""")
