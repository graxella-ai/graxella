"""Default benchmark runner.

Runs the labeled task suite through agent2society and the supervisor baseline,
then prints the comparison table.

Usage:
    python benchmarks/run.py

For real-LLM measurements, edit this script to pass a `chat_fn` that
hits your provider's chat-completion endpoint. Example for the OpenAI
SDK:

    from openai import OpenAI
    client = OpenAI()

    def chat_fn(messages):
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            response_format={"type": "json_object"},
        )
        return {
            "content": r.choices[0].message.content,
            "usage": {
                "prompt_tokens": r.usage.prompt_tokens,
                "completion_tokens": r.usage.completion_tokens,
            },
        }

The harness will trust whatever usage data the provider returns.
"""
from __future__ import annotations

from agent2society.bench import Bench, default_mesh_cards, default_suite


def _attach_handlers(bench: Bench) -> None:
    """Register dummy handlers so dispatch succeeds in-process.

    The benchmark measures coordination cost, not task quality — so the
    actual agent work is just an echo. Dispatch cost is the same for both
    methods so it nets out.
    """
    for node in bench._mesh.graph.agents():
        url = node.url

        def make_handler(agent_name: str):
            def handler(_url, payload):
                msg_id = payload.get("id", "0")
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "role": "agent",
                        "parts": [{"kind": "text", "text": f"[{agent_name}] done"}],
                    },
                }

            return handler

        # Only register if no handler is already wired (e.g. by an adapter).
        if url not in bench._mesh._local._handlers:
            bench._mesh._local.register(url, make_handler(node.name))


def main() -> None:
    bench = Bench(
        cards=default_mesh_cards(),
        tasks=default_suite(),
    )
    _attach_handlers(bench)
    result = bench.run()
    print(result.render())


if __name__ == "__main__":
    main()
