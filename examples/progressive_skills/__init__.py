"""Progressive Skill Disclosure demo.

Same open-source model (qwen2.5:3b) run two ways:

  01_flat_binding      — all 10 skills bound to the LLM (Anthropic-style
                         in-context progressive disclosure). Large prompt,
                         degraded selection accuracy on small models.
  02_graxella          — the same 10 skills live in a registry. A TF-IDF
                         router pre-selects the top-3 for each query;
                         approved (query -> skills) picks are cached in a
                         Rulebook and served without any LLM call on repeat.

The comparison script prints tokens-to-LLM, selection accuracy, wall time,
and projected $/task at frontier-model prices — the "intelligence per
dollar" evidence that the orchestration layer is doing the work the model
would otherwise have to do.
"""
