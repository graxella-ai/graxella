"""axon-fabric — backward-compatibility shell for the tool boundary.

The Phase 2 tool-boundary work (drift interception, the heal ladder,
cited tool trust) was folded into the graxella package itself so that
users import ONE package:

    from graxella.healing import ToolInterceptor, tool_trust

``axon_fabric.interceptor`` and ``axon_fabric.trust`` remain as thin
re-export shims for anything already written against the old paths.
This package otherwise reserves the workspace seat for future
tool-boundary work that does not belong in the user-facing surface
(sentinel canary calls, schema differ, federated schema registry).
"""

__version__ = "0.0.0"
