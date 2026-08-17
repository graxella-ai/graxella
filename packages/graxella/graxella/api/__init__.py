"""graxella.api — FastAPI surface + React operator dashboard.

Serves the operator-facing view of an instrumented graxella runtime:
  * pending promotions (approve / reject / rollback)
  * belief timeline and why-believed queries
  * constitution current / diff / violations
  * routing decision explorer with counterfactual replay
  * live governance detector stream

Beat 1 ships FastAPI routes + OpenAPI schema. The React app is scaffolded
under ``graxella/api/ui/`` and served under ``/dashboard``; Beat 1.5
fleshes out the UI.
"""
from graxella.api.app import create_app

__all__ = ["create_app"]
