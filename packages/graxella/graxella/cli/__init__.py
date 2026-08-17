"""graxella.cli — argparse entrypoints.

The console script `graxella` (declared in pyproject.toml) resolves to
`graxella.cli.main:main`. `python -m graxella.cli` also works.
"""
from graxella.cli.main import main

__all__ = ["main"]
