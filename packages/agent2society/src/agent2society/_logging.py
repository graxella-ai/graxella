"""Internal logging helper.

agent2society follows the standard library convention of attaching a
NullHandler to its package logger so importing the library never produces
output on its own. Production callers configure handlers/levels on the
"agent2society" logger.

Use `get_logger(__name__)` from any module to get a child logger.
"""
from __future__ import annotations

import logging


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    return logger


# Attach a NullHandler at the package root so first-import side effects
# stay quiet. Library code never calls logging.basicConfig().
_root = logging.getLogger("agent2society")
if not any(isinstance(h, logging.NullHandler) for h in _root.handlers):
    _root.addHandler(logging.NullHandler())
