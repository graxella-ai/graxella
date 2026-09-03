"""Single source of truth for the package version.

pyproject.toml reads `__version__` from this module via
setuptools' `dynamic = ["version"]` mechanism, so bumping here bumps
everywhere.
"""
__version__ = "0.1.1"
