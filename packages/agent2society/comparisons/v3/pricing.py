"""Pricing for v3: real Qwen runs extrapolated to commercial-model costs."""

from __future__ import annotations

from typing import Dict


# USD per 1,000,000 tokens.
PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o":            {"input": 5.00,  "output": 15.00},
    "gpt-4o-mini":       {"input": 0.15,  "output": 0.60},
    "claude-sonnet-4":   {"input": 3.00,  "output": 15.00},
    "claude-opus-4":     {"input": 15.00, "output": 75.00},
    "qwen2.5:7b-local":  {"input": 0.00,  "output": 0.00},
}


def cost(input_tokens: int, output_tokens: int, tier: str) -> float:
    """USD cost for `input_tokens` + `output_tokens` at the given tier."""
    p = PRICING[tier]
    return (input_tokens / 1_000_000.0) * p["input"] + (output_tokens / 1_000_000.0) * p["output"]


def all_costs(input_tokens: int, output_tokens: int) -> Dict[str, float]:
    return {tier: cost(input_tokens, output_tokens, tier) for tier in PRICING}
