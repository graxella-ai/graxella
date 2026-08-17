"""graxella.integrations — adapters that glue graxella into host runtimes.

One submodule per host runtime. Each is optional: import from
`graxella.integrations.<host>` only if you use that host.

Available adapters:
    graxella.integrations.langgraph  — LangChain / LangGraph callback handler.
    graxella.integrations.axon       — B8 AXON runtime dispatch instrumenter.
    graxella.integrations.mcp        — MCP stdio server + handler registry.
"""
