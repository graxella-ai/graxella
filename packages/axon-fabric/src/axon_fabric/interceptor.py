"""axon_fabric.interceptor — backward-compatibility shim.

The canonical home is :mod:`graxella.healing.interceptor`. Everything a
user touches lives in the graxella package; import from there:

    from graxella.healing import ToolInterceptor
"""
from graxella.healing.interceptor import (DRIFT_SIGNATURE, Healer,
                                          ToolInterceptor, is_drift)

__all__ = ["ToolInterceptor", "Healer", "is_drift", "DRIFT_SIGNATURE"]
