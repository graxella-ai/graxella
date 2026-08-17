"""Boots / health-checks / shuts down the 7 v3 worker agents."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from comparisons.v3.agents import AGENTS_V3

ROOT = Path(__file__).resolve().parents[2]


class ServerManager:
    def __init__(self, agents: Optional[List[str]] = None, startup_timeout: float = 60.0):
        self._agent_names = list(agents) if agents else list(AGENTS_V3.keys())
        self._procs: Dict[str, subprocess.Popen] = {}
        self._startup_timeout = startup_timeout

    def __enter__(self) -> "ServerManager":
        env = os.environ.copy()
        # Make sure subprocesses can resolve `comparisons.v3.*`.
        existing = env.get("PYTHONPATH", "")
        extra = str(ROOT)
        if extra not in existing.split(os.pathsep):
            env["PYTHONPATH"] = (
                extra + (os.pathsep + existing if existing else "")
            )

        for name in self._agent_names:
            cfg = AGENTS_V3[name]
            module = f"comparisons.v3.agents.{name}"
            print(f"[server_manager] starting {name} on :{cfg['port']} ...", flush=True)
            proc = subprocess.Popen(
                [sys.executable, "-m", module],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._procs[name] = proc

        # Health-check each
        for name in self._agent_names:
            cfg = AGENTS_V3[name]
            self._wait_for_card(name, cfg["port"])
        print("[server_manager] all agents healthy.", flush=True)
        return self

    def __exit__(self, *args) -> None:
        for name, proc in list(self._procs.items()):
            try:
                proc.terminate()
            except Exception:
                pass
        deadline = time.time() + 5.0
        for name, proc in self._procs.items():
            remaining = max(0.1, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _wait_for_card(self, name: str, port: int) -> None:
        url = f"http://localhost:{port}/.well-known/agent-card.json"
        deadline = time.time() + self._startup_timeout
        last_err = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2.0) as r:
                    if r.status == 200:
                        return
            except Exception as exc:
                last_err = exc
            # Check the subprocess hasn't died.
            proc = self._procs.get(name)
            if proc is not None and proc.poll() is not None:
                raise RuntimeError(
                    f"{name} subprocess exited early (code={proc.returncode}); "
                    f"last error fetching card: {last_err}"
                )
            time.sleep(0.5)
        raise RuntimeError(f"{name} did not come up on :{port}: {last_err}")

    def agent_urls(self) -> Dict[str, str]:
        return {
            name: f"http://localhost:{AGENTS_V3[name]['port']}"
            for name in self._agent_names
        }
