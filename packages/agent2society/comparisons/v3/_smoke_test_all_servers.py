"""Phase 2 smoke: bring up all 7 worker agents and health-check them."""

from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from comparisons.v3.server_manager import ServerManager


def main() -> int:
    t0 = time.perf_counter()
    with ServerManager(startup_timeout=120.0) as mgr:
        cold = (time.perf_counter() - t0) * 1000.0
        urls = mgr.agent_urls()
        for name, url in urls.items():
            with urllib.request.urlopen(f"{url}/.well-known/agent-card.json", timeout=2) as r:
                body = r.read().decode()
            print(f"[all_servers] {name} card-len={len(body)}")
    print(f"[all_servers] cold_start_total_ms={cold:.0f}")
    print("[all_servers] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
