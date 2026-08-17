"""MCP binding for Graxella.

Two submodules:
  * handlers  — pure-Python callables, no MCP dependency (unit-testable).
  * server    — MCP tool advertisement + stdio transport (lazy `mcp` import).

Public surface is intentionally small. Most callers reach for either
`graxella.integrations.mcp.handlers.HANDLERS` (embedding) or
`graxella.integrations.mcp.server.serve_stdio` (running the server).
"""
from graxella.integrations.mcp.handlers import HANDLERS

__all__ = ["HANDLERS"]
