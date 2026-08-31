"""graxella.api.studio — click a pipeline node, get a notebook to build it.

The Stage-3 authoring interaction: clicking a node in the governance UI
scaffolds a per-node Jupyter notebook (if it doesn't exist yet) and launches
it in the developer's editor. Launch order, best first:

  1. VS Code  (`code -r <notebook>`) — opens in the running IDE as a notebook.
  2. Jupyter Lab (`jupyter-lab <notebook>`) — opens its own browser tab.
  3. neither — return the path for the developer to open manually.

The scaffold bootstraps ``import graxella`` from the source tree so the
notebook runs without an install, then gives a node stub to implement and a
local test cell. The ``graxella.submit(...)`` compile step is the Stage-3
authoring API (marked as forthcoming); everything else runs today.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _jupyter_lab_cmd() -> list[str] | None:
    """Base command to launch Jupyter Lab, or None if unavailable."""
    p = shutil.which("jupyter-lab")
    if p:
        return [p]
    j = shutil.which("jupyter")
    if j:
        return [j, "lab"]
    return None


class JupyterServer:
    """A single managed Jupyter Lab process whose URL + token we own, so the
    UI can show a copy-pasteable link and open node notebooks against it."""

    _inst: "JupyterServer | None" = None

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.token: str | None = None
        self.root: str | None = None

    @classmethod
    def get(cls) -> "JupyterServer":
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def _ping(self) -> bool:
        if not self.port:
            return False
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api", timeout=1.5) as r:
                return r.status == 200
        except Exception:
            return False

    def is_running(self) -> bool:
        return (self.proc is not None and self.proc.poll() is None
                and self._ping())

    def start(self, root: str | Path, *, timeout: float = 30.0) -> dict:
        if self.is_running():
            return self.info()
        base = _jupyter_lab_cmd()
        if not base:
            return {"running": False,
                    "error": "Jupyter Lab not found — pip install jupyterlab"}
        self.token = secrets.token_hex(16)
        self.port = _free_port()
        self.root = str(Path(root).resolve())
        Path(self.root).mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["JUPYTER_TOKEN"] = self.token
        cmd = base + ["--no-browser", f"--port={self.port}",
                      f"--ServerApp.root_dir={self.root}",
                      "--ServerApp.open_browser=False"]
        try:
            self.proc = subprocess.Popen(
                cmd, env=env, cwd=self.root,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            return {"running": False, "error": str(exc)}
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                return {"running": False,
                        "error": "jupyter exited during startup"}
            if self._ping():
                break
            time.sleep(0.5)
        return self.info()

    def base_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.port else None

    def url(self) -> str | None:
        return (f"{self.base_url()}/lab?token={self.token}"
                if self.is_running() else None)

    def url_for(self, path: str | Path) -> str | None:
        if not self.is_running() or not self.root:
            return None
        try:
            rel = Path(path).resolve().relative_to(self.root)
        except ValueError:
            return self.url()
        rp = str(rel).replace("\\", "/")
        return f"{self.base_url()}/lab/tree/{rp}?token={self.token}"

    def info(self) -> dict:
        run = self.is_running()
        return {
            "running": run,
            "available": _jupyter_lab_cmd() is not None,
            "url": self.url() if run else None,
            "base_url": self.base_url() if run else None,
            "token": self.token if run else None,
            "port": self.port if run else None,
            "root": self.root if run else None,
        }

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def _safe(node_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", node_id) or "node"


def find_code() -> str | None:
    """Path to the VS Code CLI, if present."""
    for name in ("code", "code-insiders"):
        p = shutil.which(name)
        if p:
            return p
    # common Windows install location
    win = Path.home() / "AppData/Local/Programs/Microsoft VS Code/bin/code.cmd"
    return str(win) if win.exists() else None


def find_jupyter() -> str | None:
    for name in ("jupyter-lab", "jupyter"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _nb_code(*lines: str) -> dict:
    src = "\n".join(lines)
    return {"cell_type": "code", "metadata": {}, "execution_count": None,
            "outputs": [], "source": src.splitlines(keepends=True) or [src]}


def _nb_md(*lines: str) -> dict:
    src = "\n".join(lines)
    return {"cell_type": "markdown", "metadata": {},
            "source": src.splitlines(keepends=True) or [src]}


def scaffold_node_notebook(notebook_dir: Path, pkg_root: Path, node: dict,
                           pipeline: str | None) -> Path:
    """Create notebooks/<node>.ipynb if missing. Returns its path."""
    notebook_dir.mkdir(parents=True, exist_ok=True)
    path = notebook_dir / f"{_safe(node['id'])}.ipynb"
    if path.exists():
        return path

    nid, label = node["id"], node.get("label", node["id"])
    subtitle, kind = node.get("subtitle", ""), node.get("kind", "tool")
    boot = repr(str(pkg_root))

    nb = {
        "cells": [
            _nb_md(f"# Node · {label}",
                   f"`id: {nid}`  ·  kind: **{kind}**  ·  {subtitle}",
                   "",
                   f"Part of pipeline **{pipeline or '(unnamed)'}**. Build and "
                   "test this node here; it will compile into the pipeline.",
                   "",
                   "> Cells 1–2 run today. Cell 3 (`graxella.submit`) is the "
                   "Stage-3 authoring API — marked forthcoming."),
            _nb_code("# bootstrap — make graxella importable from source (dev)",
                     f"import sys; sys.path.insert(0, {boot})",
                     "import graxella",
                     "print('graxella', graxella.__version__)"),
            _nb_code(f"# 1 · implement the node  ({label})",
                     "def run(payload: dict) -> dict:",
                     f'    """{label}: {subtitle}"""',
                     "    # TODO: your logic here",
                     "    return {**payload, "
                     f"'node': {nid!r}, 'ok': True}}",
                     "",
                     "run({'demo': True})"),
            _nb_code("# 2 · test locally against an in-memory ledger",
                     "from graxella.beliefs import Memory",
                     "m = Memory.sqlite(':memory:', agent_id='studio')",
                     f"aid = m.record_decision(decision_type='tool', "
                     f"task='test', chosen={nid!r}, domain='studio')",
                     "m.record_outcome(decision_id=aid, ok=True, kind='tool', "
                     f"chosen={nid!r}, domain='studio', session_id='s0')",
                     "print('recorded outcome for', "
                     f"{nid!r}, '· beliefs:', len(m.beliefs()))"),
            _nb_md("### 3 · submit  *(Stage-3 authoring API — forthcoming)*",
                   "```python",
                   "graxella.submit(run)   # compiles this node into "
                   "generate.py; code stays the source of truth",
                   "```"),
        ],
        "metadata": {"language_info": {"name": "python"},
                     "kernelspec": {"name": "python3",
                                    "display_name": "Python 3"}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    path.write_text(json.dumps(nb, indent=1), encoding="utf-8")
    return path


def open_node(notebook_dir: Path, pkg_root: Path, node: dict,
              pipeline: str | None, *, prefer: str = "auto") -> dict[str, Any]:
    """Scaffold the node's notebook and launch it. Returns a result dict."""
    path = scaffold_node_notebook(notebook_dir, pkg_root, node, pipeline)
    rel = str(path)

    def _launch_code() -> bool:
        exe = find_code()
        if not exe:
            return False
        try:
            subprocess.Popen([exe, "-r", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False

    def _open_jupyter() -> str | None:
        """Ensure the managed Jupyter server is up and return the node URL."""
        srv = JupyterServer.get()
        if not srv.is_running():
            srv.start(notebook_dir.parent)
        return srv.url_for(path)

    # If the managed Jupyter server is already running, always route there —
    # the developer chose Jupyter by starting it from the footer.
    if prefer == "jupyter" or JupyterServer.get().is_running():
        url = _open_jupyter()
        if url:
            return {"opened": True, "mode": "jupyter", "url": url,
                    "path": rel, "node": node["id"]}

    if prefer in ("auto", "code") and _launch_code():
        return {"opened": True, "mode": "vscode", "path": rel,
                "node": node["id"]}

    return {"opened": False, "mode": "file", "path": rel, "node": node["id"]}


__all__ = ["scaffold_node_notebook", "open_node", "find_code", "find_jupyter"]
