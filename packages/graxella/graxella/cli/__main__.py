"""Enables `python -m graxella.cli` in addition to the `graxella` console script."""
from graxella.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
