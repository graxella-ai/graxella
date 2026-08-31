"""Suite-wide fixtures.

The routing default is embedding-first (``router="auto"``). The unit
suite pins the lexical path instead: deterministic scores, no network,
no model downloads — tests that specifically exercise semantic routing
opt back in by clearing the override (see test_semantic_routing.py).
"""
import os

os.environ.setdefault("GRAXELLA_ROUTER", "tfidf")
