# Mnema

An experiential-memory runtime for autonomous agents: assertions in, beliefs out, audit always.

Today's agents are permanent first-day employees with excellent educations.
Mnema is the substrate that lets them become experts.

## Layout
- `src/mnema/core` — frozen domain model (pydantic + stdlib only)
- `src/mnema/ports` — Protocols (storage, embedding)
- `src/mnema/adapters` — implementations (sqlite first)
- `tests` — property tests ARE the schema spec
- `docs/adr` — decisions

## Dev
    pip install -e ".[dev]"
    pytest
