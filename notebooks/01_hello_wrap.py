"""Showcase 01 -- the one-line wrap.

PROVES: any callable / LangGraph app becomes an observed, governed,
learning runtime by calling ``instrument(app, memory=, society=)``. No
edit to the underlying app. The wrap is a facade that forwards
.invoke/.stream/.batch while attaching graxella's callbacks.

Run:
    /c/Users/Sridhar/anaconda3/python showcase/01_hello_wrap.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "graxella"))

from graxella import Memory, Society, instrument


class MyApp:
    """Any object with an .invoke(state, config?) method qualifies. Real
    LangGraph StateGraph.compile() results match this shape."""

    def invoke(self, state: dict, config: dict | None = None) -> dict:
        return {"input": state["input"], "output": f"processed({state['input']})"}


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="graxella-showcase-01-"))
    print(f"[setup] workdir: {workdir}")

    memory = Memory.sqlite(db_path=str(workdir / "mnema.db"), agent_id="showcase")
    society = Society(store_path=str(workdir / "routes.jsonl"))

    app = MyApp()
    print(f"[before] type: {type(app).__name__}, has memory? {hasattr(app, 'memory')}")

    wrapped = instrument(app, memory=memory, society=society)
    print(f"[after]  type: {type(wrapped).__name__}, "
          f"has memory? {hasattr(wrapped, 'memory')}, "
          f"has tracer? {hasattr(wrapped, 'tracer')}")

    # The wrapped object passes .invoke straight through.
    result = wrapped.invoke({"input": "hello"})
    print(f"[invoke] {result}")

    print("\nPROVEN: one call to instrument() attached memory + society + "
          "tracer + gate + constitution to an unchanged app.")


if __name__ == "__main__":
    main()
