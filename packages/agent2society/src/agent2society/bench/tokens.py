"""Token counting.

Uses `tiktoken` when available (accurate, model-specific). Falls back to a
4-chars-per-token English estimate otherwise. Numbers from the fallback are
fine for relative comparisons but should be re-run with tiktoken before
quoting absolute counts.
"""
from __future__ import annotations

from typing import Callable, Optional

_TIKTOKEN_AVAILABLE: Optional[bool] = None
_ENCODER = None


def _try_load_encoder(model_hint: str = "gpt-4o-mini"):
    global _TIKTOKEN_AVAILABLE, _ENCODER
    if _TIKTOKEN_AVAILABLE is False:
        return None
    try:
        import tiktoken  # type: ignore

        _TIKTOKEN_AVAILABLE = True
        try:
            return tiktoken.encoding_for_model(model_hint)
        except KeyError:
            return tiktoken.get_encoding("cl100k_base")
    except ImportError:
        _TIKTOKEN_AVAILABLE = False
        return None


def count_tokens(text: str, *, model_hint: str = "gpt-4o-mini") -> int:
    """Return an integer token count for `text`.

    Uses tiktoken if installed (`pip install agent2society[bench]`). Otherwise
    falls back to `len(text) // 4` rounded up — a conservative English
    approximation.
    """
    if not text:
        return 0
    enc = _try_load_encoder(model_hint)
    if enc is not None:
        return len(enc.encode(text))
    return max(1, (len(text) + 3) // 4)


TokenFn = Callable[[str], int]


def make_token_fn(model_hint: str = "gpt-4o-mini") -> TokenFn:
    """Return a closure bound to a specific tokenizer."""

    def _fn(text: str) -> int:
        return count_tokens(text, model_hint=model_hint)

    return _fn


def has_tiktoken() -> bool:
    """Report whether tiktoken is available — useful for benchmark headers."""
    _try_load_encoder()
    return bool(_TIKTOKEN_AVAILABLE)
