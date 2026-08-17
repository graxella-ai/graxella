"""Seed mnema.db with weatherlib drift scenario so CLI commands show real data."""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from mnema.adapters.sqlite.repository import SqliteMnemaStore
from mnema.adapters.llm.fake import FakeLLM
from mnema.services.recorder import MemoryRecorder
from mnema.services.consolidator import SleepConsolidator

DB = "mnema.db"
AGENT = "agent-1"

store = SqliteMnemaStore(f"sqlite:///{DB}")
rec = MemoryRecorder(store)

print(f"Seeding {DB} with agent '{AGENT}'...")

# Phase 1 -- v2 drift observations
a1 = rec.observe(AGENT, "weatherlib.get_weather() raises AttributeError in v2 -- removed", subject="weatherlib")
a2 = rec.observe(AGENT, "weatherlib.fetch_forecast(city) is the correct v2 API call", subject="weatherlib")
a3 = rec.observe(AGENT, "weatherlib v2 migration: replace get_weather() with fetch_forecast(city)", subject="weatherlib")
print(f"  Recorded 3 beliefs: {a1.id[:16]}... {a2.id[:16]}... {a3.id[:16]}...")

# Sleep consolidation -> digest v1
llm_v2 = FakeLLM.from_scenario("weatherlib_v2")
d1 = SleepConsolidator(store, llm_v2).consolidate(AGENT)
assert d1 is not None
print(f"  Consolidated -> digest v1: {len(d1.rules)} rule(s), {len(d1.skills)} skill(s)")

# Phase 2 -- v3 drift observations
a4 = rec.observe(AGENT, "weatherlib.fetch_forecast() removed in v3 -- AttributeError", subject="weatherlib")
a5 = rec.observe(AGENT, "weatherlib v3 OOP: WeatherClient class introduced", subject="weatherlib")
a6 = rec.observe(AGENT, "weatherlib.WeatherClient().fetch(city) is the v3 API entry point", subject="weatherlib")
print(f"  Recorded 3 more beliefs for v3 drift...")

# Sleep consolidation -> digest v2
llm_v3 = FakeLLM.from_scenario("weatherlib_v3")
d2 = SleepConsolidator(store, llm_v3).consolidate(AGENT)
assert d2 is not None
print(f"  Consolidated -> digest v2: {len(d2.rules)} rule(s), {len(d2.skills)} skill(s)")

# Retract one old belief to show cascade
rec.retract(a1.id)
print(f"  Retracted belief {a1.id[:16]}... (old error observation)")

print(f"\nDone. Run CLI commands with:  .venv/Scripts/mnema.exe --db {DB} <command> ...")
print(f"\nAssertion IDs for 'why' command:")
for label, a in [("a1(retracted)", a1), ("a2", a2), ("a3", a3), ("a4", a4), ("a5", a5), ("a6", a6)]:
    print(f"  {label:20s}  {a.id}")
