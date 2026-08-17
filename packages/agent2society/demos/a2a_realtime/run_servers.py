"""Boot all 4 A2A agent servers on background threads.

Used by compare.py and standalone for manual smoke testing:

    python run_servers.py    # keeps servers alive in foreground
"""
from __future__ import annotations

import logging
import time
from typing import List, Tuple

# The in-memory event queue logs a benign WARNING about the dispatcher
# task whenever a non-streaming SendMessage returns synchronously. It
# does not affect delivery -- silence it so the comparison output is clean.
logging.getLogger("a2a.server.events.event_queue_v2").setLevel(logging.ERROR)

from agents import (
    intent_classifier,
    kb_lookup,
    escalation_handler,
    response_generator,
)
from agents._runtime import build_app, serve_in_thread


AGENT_MODULES = [
    intent_classifier,
    kb_lookup,
    escalation_handler,
    response_generator,
]


def start_all() -> List[Tuple]:
    handles = []
    for mod in AGENT_MODULES:
        app = build_app(mod.CARD, mod.Executor())
        server, thread = serve_in_thread(app, mod.PORT)
        handles.append((mod, server, thread))
        print(f"  [up] {mod.CARD.name:22s} {mod.URL}")
    return handles


def stop_all(handles) -> None:
    for _mod, server, thread in handles:
        server.should_exit = True
    for _mod, _server, thread in handles:
        thread.join(timeout=2)


if __name__ == "__main__":
    print("Starting customer-support A2A agents...")
    handles = start_all()
    print("\nAll 4 agents serving real A2A JSON-RPC. Ctrl+C to exit.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        stop_all(handles)
