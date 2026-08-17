"""axon-fabric — graxella's tool-boundary fabric (shell package).

Phase 2 (build plan tasks 2-4, 2-5, 2-6) ports the B10 work here:

  * host middleware   — MCP drift interception around tool clients
  * healer pipeline   — LLM-once -> compiler -> verifier -> deterministic
                        transform, shipped as Proposal(kind=transform)
                        through the Evidence Gate
  * sentinel          — canary calls, schema differ, drift forecasting
  * registry          — federated schema registry feeding tool trust scores

Until then this package is intentionally empty: it reserves the name and
the workspace seat so nothing else squats on the tool boundary.
"""

__version__ = "0.0.0"
