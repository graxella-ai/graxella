# Contributing to graxella

Thanks for looking. This is a small project with a specific thesis, so
this file is mostly about *what kind* of change fits — the mechanics are
short.

## Getting set up

```bash
git clone https://github.com/graxella-ai/graxella
cd graxella
uv sync                      # one venv: graxella + agent2society, editable
uv run pytest                # the full suite (~290 tests, no LLM needed)
```

Tutorials 01–06 and 09 run with no model at all. 07–08, 10 and the
capstone notebook 11 need a local [Ollama](https://ollama.com); each one
checks and exits politely if it is missing.

## The one rule that explains most review comments

**The LLM may propose. The evidence decides.**

Routing, promotion, demotion, recall injection, and every governance
verdict are deterministic and recorded. There is exactly one place a
model is allowed to appear in a decision path — the drift healer's
proposal step — and even there its output is validated against the real
fallback, cached as a deterministic recipe, and sent to the Evidence
Gate for review before it is ever trusted twice.

A change that puts a model call inside a decision loop will be asked to
justify itself hard, or turned into a proposal that the gate evaluates.

## Honesty contract

This is the part that matters most, and it is not decoration.

- **Numbers in docs come from runs that actually happened.** If you add
  a claim, add the script that produces it. `benchmarks/eval_harness.py`
  is the CI-checked version of this.
- **Keep the unflattering result.** Tutorial 11 keeps a heal that failed
  with the default model, and a hallucination probe where the governed
  side looked no better than the raw one. That is deliberate. Do not
  "fix" a demo by re-rolling it until it looks good.
- **Never claim a capability that is only discussed.** If it is not
  built and tested, it does not go in a README, a docstring, or a page.
- **Failures must be loud.** Degrading silently is a bug here, even when
  the degraded path "works". Look at how the router warns when it falls
  back to lexical matching, or how rung 2 warns when `with_skill` can't
  be resolved.

## Before you open a PR

```bash
uv run pytest                                    # everything green
uv run ruff check .                              # lint
uv run python benchmarks/eval_harness.py         # the claims scorecard
```

New behavior needs a test. Bug fixes need a test that fails before the
fix — the five fixes in the "external review" commit each have one, and
they are a good model to copy.

## Commit messages

Say what changed and *why it was wrong before*. The git log is part of
how this project explains itself; a reader should be able to follow the
reasoning without opening the diff.

## Reporting something broken

Open an issue with the smallest reproduction you can manage, the graxella
version (`python -c "import graxella; print(graxella.__version__)"`), and
what you expected instead. If it is a governance question — "why did it
promote that?" — include the output of `grx.why(proposal)`, which is
designed to answer exactly that.

## Scope

graxella is a governance layer, not an agent framework. It deliberately
does not ship an agent class, a prompt library, or a model wrapper — you
bring LangChain, LangGraph, or your own, and graxella governs underneath.
Proposals that pull it toward being a framework are likely to be
declined, however good they are on their own terms.
