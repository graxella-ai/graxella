"""MCP tool server for the incident-response lab.

Exposes five ops tools over streamable-http (the transport real deployments use).
Run:  python mcp_ops_server.py   ->  http://127.0.0.1:8901/mcp
"""
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ops-tools", host="127.0.0.1", port=8901)

# ---- synthetic incident world -------------------------------------------------
# Ground truth: payment-gateway v2.14.1 (deployed 09:35 UTC) leaks DB connections
# -> pool exhaustion -> checkout-api latency/error spike. redis is a red herring.

METRICS = {
    "checkout-api": {
        "p99_latency_ms": 4180, "baseline_p99_ms": 510, "error_rate_pct": 12.4,
        "note": "errors began 09:41 UTC, all 502/504 from upstream calls",
    },
    "payment-gateway": {
        "p99_latency_ms": 3920, "baseline_p99_ms": 300, "error_rate_pct": 9.8,
        "db_connection_pool": {"in_use": 200, "max": 200, "wait_queue": 341},
        "note": "pool saturated since 09:39 UTC; connections not being released",
    },
    "inventory-svc": {"p99_latency_ms": 88, "baseline_p99_ms": 85, "error_rate_pct": 0.1},
    "redis-cache": {
        "p99_latency_ms": 4, "baseline_p99_ms": 3, "memory_used_pct": 97,
        "evictions_per_min": 220, "note": "memory high for 3 weeks; hit rate stable at 94%",
    },
    "bank-connector": {"p99_latency_ms": 240, "baseline_p99_ms": 235, "error_rate_pct": 0.2},
}

DEPLOYS = {
    "payment-gateway": [
        {"version": "v2.14.1", "at": "09:35 UTC today", "change": "refactor: async DB connection pool handling"},
        {"version": "v2.14.0", "at": "6 days ago", "change": "add settlement retries"},
    ],
    "checkout-api": [{"version": "v8.2.0", "at": "3 days ago", "change": "copy changes on receipt page"}],
    "inventory-svc": [{"version": "v3.1.9", "at": "12 days ago", "change": "index rebuild job"}],
    "redis-cache": [], "bank-connector": [],
}

DEPENDENCIES = {
    "checkout-api": ["payment-gateway", "inventory-svc", "redis-cache"],
    "payment-gateway": ["redis-cache", "bank-connector"],
    "inventory-svc": ["redis-cache"],
}

RUNBOOKS = [
    {"id": "RB-101", "title": "Database connection pool exhaustion",
     "steps": ["confirm pool in_use == max and wait_queue growing",
               "identify the release that changed pool/connection handling",
               "roll back that release (see RB-033)",
               "if rollback impossible: bump pool max 2x as a stopgap and recycle pods"]},
    {"id": "RB-207", "title": "Redis memory pressure",
     "steps": ["check eviction rate and hit rate", "if hit rate stable, schedule capacity work - NOT an incident page",
               "if hit rate collapsing, scale replica and warm cache"]},
    {"id": "RB-033", "title": "Standard rollback procedure",
     "steps": ["freeze deploys for the service", "helm rollback <service> to previous revision",
               "watch golden signals for 15 min", "file post-incident ticket with root cause"]},
]

TICKETS: list[dict] = []

# ---- tools --------------------------------------------------------------------

@mcp.tool()
def get_service_metrics(service: str) -> str:
    """Current golden-signal metrics for a service (latency, errors, saturation)."""
    m = METRICS.get(service)
    return json.dumps(m, indent=1) if m else f"unknown service '{service}'. known: {sorted(METRICS)}"

@mcp.tool()
def get_dependency_map(service: str) -> str:
    """Downstream dependencies of a service (what it calls)."""
    return json.dumps({"service": service, "calls": DEPENDENCIES.get(service, [])})

@mcp.tool()
def get_recent_deploys(service: str) -> str:
    """Recent deploys for a service, newest first."""
    return json.dumps(DEPLOYS.get(service, []), indent=1)

@mcp.tool()
def search_runbooks(query: str) -> str:
    """Keyword search over incident runbooks; returns matching runbooks with steps."""
    q = query.lower()
    hits = [rb for rb in RUNBOOKS if any(w in (rb["title"] + " " + " ".join(rb["steps"])).lower()
                                         for w in q.split())]
    return json.dumps(hits or RUNBOOKS, indent=1)

@mcp.tool()
def create_ticket(title: str, severity: str, body: str) -> str:
    """File an incident ticket. severity: sev1|sev2|sev3."""
    t = {"id": f"OPS-{1000 + len(TICKETS)}", "title": title, "severity": severity, "body": body}
    TICKETS.append(t)
    return json.dumps(t)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
