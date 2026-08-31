"""axon_fabric.trust — backward-compatibility shim.

The canonical home is :mod:`graxella.healing.trust`. Everything a user
touches lives in the graxella package; import from there:

    from graxella.healing import tool_trust, preferred
"""
from graxella.healing.trust import ToolTrust, preferred, tool_trust

__all__ = ["ToolTrust", "tool_trust", "preferred"]
