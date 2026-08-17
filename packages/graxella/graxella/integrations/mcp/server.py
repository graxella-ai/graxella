"""MCP server binding for Graxella — expose the agenda over stdio.

Any MCP-aware client (Claude Desktop, Cursor, your own agent) can now:
  * see pending proposals               -> tool: `list_proposals`
  * promote a rule                      -> tool: `promote_proposal`
  * reject a proposal                   -> tool: `reject_proposal`
  * inspect the approved rulebook       -> tool: `list_rules`
  * query lived experience              -> tool: `query_episodes`
  * search seeded documentation         -> tool: `query_seed`
  * export a PROV-O audit bundle        -> tool: `export_audit`

MCP is imported lazily inside `serve_stdio()` so plain use of the handler
functions doesn't require the SDK to be installed.

Client config snippet (Claude Desktop, etc.):
    {
      "mcpServers": {
        "graxella": {
          "command": "python",
          "args": ["-m", "graxella.integrations.mcp.server"]
        }
      }
    }
"""
from __future__ import annotations

import json
from typing import Any

from graxella.integrations.mcp.handlers import HANDLERS

# JSON-Schema shapes for each handler — reused by both the MCP tool
# advertisement AND unit tests. Keep them narrow: MCP clients render these
# to end-users, so descriptions matter.
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_proposals": {
        "description": "List proposals from the hidden agenda (miners + optional docs). "
                       "Each is annotated with rulebook status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "store_path":     {"type": "string", "description": "Path to the SQLite experience store."},
                "docs_path":      {"type": "string", "description": "Optional: path to a docs tree; adds DocsMiner proposals."},
                "rulebook_path":  {"type": "string", "description": "Optional: path to the rulebook JSON for status annotation."},
                "include_rejected": {"type": "boolean", "default": False},
            },
            "required": ["store_path"],
        },
    },
    "promote_proposal": {
        "description": "Promote a proposal to an approved rule (idempotent).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "store_path":    {"type": "string"},
                "rulebook_path": {"type": "string"},
                "proposal_id":   {"type": "string", "description": "prop_xxx"},
                "reviewer":      {"type": "string", "default": "human",
                                  "description": "Handle recorded on the ApprovedRule."},
                "docs_path":     {"type": "string",
                                  "description": "Include this if promoting a docs-derived proposal."},
            },
            "required": ["store_path", "rulebook_path", "proposal_id"],
        },
    },
    "reject_proposal": {
        "description": "Mark a proposal as rejected; future lists hide it by default.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rulebook_path": {"type": "string"},
                "proposal_id":   {"type": "string"},
            },
            "required": ["rulebook_path", "proposal_id"],
        },
    },
    "list_rules": {
        "description": "Return every ApprovedRule in the rulebook.",
        "inputSchema": {
            "type": "object",
            "properties": {"rulebook_path": {"type": "string"}},
            "required": ["rulebook_path"],
        },
    },
    "query_episodes": {
        "description": "Filter Experience episodes by session and/or intent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "store_path": {"type": "string"},
                "session":    {"type": "string"},
                "intent":     {"type": "string"},
                "limit":      {"type": "integer", "default": 50},
            },
            "required": ["store_path"],
        },
    },
    "query_seed": {
        "description": "Search assertions extracted from a docs tree (TF-IDF-lite).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "docs_path": {"type": "string"},
                "query":     {"type": "string"},
                "k":         {"type": "integer", "default": 5},
            },
            "required": ["docs_path", "query"],
        },
    },
    "export_audit": {
        "description": "Emit a PROV-O JSON-LD audit bundle covering "
                       "episodes, proposals, promotions, and approved rules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "store_path":    {"type": "string"},
                "rulebook_path": {"type": "string"},
                "out_path":      {"type": "string"},
                "docs_path":     {"type": "string"},
            },
            "required": ["store_path", "rulebook_path", "out_path"],
        },
    },
}


def _import_mcp() -> Any:
    """Lazy MCP SDK import. Raises with an actionable install hint."""
    try:
        import mcp  # noqa: F401
        from mcp.server import Server           # noqa: F401
        from mcp.server.stdio import stdio_server  # noqa: F401
        import mcp.types as mcp_types           # noqa: F401
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "graxella.integrations.mcp.server requires the `mcp` Python SDK. "
            "Install with:  pip install graxella[mcp]"
        ) from e
    return {
        "Server": Server,
        "stdio_server": stdio_server,
        "types": mcp_types,
    }


def build_server() -> Any:
    """Build (but don't run) an MCP Server exposing every handler.

    Separated from `serve_stdio()` so it can be embedded in HTTP servers,
    tests, or any other transport MCP grows in the future.
    """
    mcp_mod = _import_mcp()
    Server = mcp_mod["Server"]
    types = mcp_mod["types"]

    server = Server("graxella")

    @server.list_tools()
    async def _list_tools() -> list[Any]:
        return [
            types.Tool(name=name,
                       description=spec["description"],
                       inputSchema=spec["inputSchema"])
            for name, spec in TOOL_SCHEMAS.items()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        handler = HANDLERS.get(name)
        if handler is None:
            payload = {"error": f"unknown tool {name!r}"}
        else:
            try:
                payload = handler(**(arguments or {}))
            except Exception as e:
                payload = {"error": type(e).__name__, "message": str(e)}
        return [types.TextContent(type="text",
                                  text=json.dumps(payload, indent=2, default=str))]

    return server


def serve_stdio() -> None:
    """Blocking — run the MCP server over stdio until the client disconnects."""
    import asyncio
    mcp_mod = _import_mcp()

    async def _run() -> None:
        server = build_server()
        async with mcp_mod["stdio_server"]() as (read, write):
            await server.run(
                read, write,
                server.create_initialization_options(),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    serve_stdio()
