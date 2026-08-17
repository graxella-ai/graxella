from __future__ import annotations

import json

from agent2society.bench import (
    Bench,
    SupervisorBaseline,
    build_supervisor_prompt,
    default_mesh_cards,
    default_suite,
    parse_supervisor_decision,
)
from agent2society.bench.tokens import count_tokens
import agent2society as r2r


def _attach_handlers(bench: Bench) -> None:
    for node in bench._mesh.graph.agents():

        def make_handler(name: str):
            def handler(_url, payload):
                return {
                    "jsonrpc": "2.0",
                    "id": payload.get("id", "0"),
                    "result": {
                        "parts": [{"kind": "text", "text": f"[{name}] done"}],
                    },
                }

            return handler

        bench._mesh._local.register(node.url, make_handler(node.name))


def test_supervisor_prompt_lists_every_agent():
    cards = default_mesh_cards()
    g = r2r.CapabilityGraph()
    for c in cards:
        g.add_agent(r2r.parse_card(c))
    msgs = build_supervisor_prompt(g, "anything")
    system = msgs[0]["content"]
    for c in cards:
        assert c["name"] in system
        for s in c["skills"]:
            assert s["id"] in system


def test_parse_supervisor_decision_handles_json_and_fences():
    a = parse_supervisor_decision('{"agent":"x","skill":"y"}')
    assert a == {"agent": "x", "skill": "y"}
    b = parse_supervisor_decision('```json\n{"agent":"x","skill":"y"}\n```')
    assert b == {"agent": "x", "skill": "y"}
    c = parse_supervisor_decision("garbage")
    assert c == {"agent": None, "skill": None}


def test_bench_runs_default_suite_and_shows_token_gap():
    bench = Bench(cards=default_mesh_cards(), tasks=default_suite())
    _attach_handlers(bench)
    result = bench.run()

    # The supervisor prompt enumerates every card — its per-task token
    # count must dwarf agent2society's single-query embedding cost.
    sup_total = result.supervisor_total_tokens()
    our_total = result.agent2society_total_tokens()
    assert sup_total > our_total * 10, (
        f"expected at least a 10x token reduction; got "
        f"supervisor={sup_total}, agent2society={our_total}"
    )

    # The package claim is *parity* in task success, not perfection — the
    # mock supervisor and agent2society share a scorer so they get the same
    # tasks right and the same tasks wrong. A real LLM might do better
    # on edge cases; this baseline is here to compare *cost*, not IQ.
    assert result.agent2society_success() == result.supervisor_success()
    n = len(result.pairs)
    # And the suite should be answerable well enough that the headline
    # is meaningful — at least 80% on lexical routing alone.
    assert result.agent2society_success() >= int(0.8 * n)

    rendered = result.render()
    assert "reduction:" in rendered
    assert "task success" in rendered


def test_real_chat_fn_usage_is_trusted():
    cards = default_mesh_cards()
    g = r2r.CapabilityGraph()
    for c in cards:
        g.add_agent(r2r.parse_card(c))

    captured = []

    def fake_chat(messages):
        captured.append(messages)
        # Always pick writer-agent to verify the harness threads the
        # supervisor's choice into dispatch.
        return {
            "content": json.dumps(
                {"agent": "writer-agent", "skill": "exec_memo"}
            ),
            "usage": {"prompt_tokens": 1234, "completion_tokens": 17},
        }

    sup = SupervisorBaseline(g, chat_fn=fake_chat)
    sup._local.register(
        "local://writer-agent",
        lambda u, p: {
            "jsonrpc": "2.0",
            "id": p.get("id", "0"),
            "result": {"parts": [{"kind": "text", "text": "memo"}]},
        },
    )
    run = sup.run("draft something")
    assert run.chosen_agent == "writer-agent"
    assert run.coord_prompt_tokens == 1234
    assert run.coord_completion_tokens == 17
    assert run.coordination_tokens == 1251
    assert run.dispatched


def test_token_counter_handles_empty_and_long_strings():
    assert count_tokens("") == 0
    assert count_tokens("hello") >= 1
    long = "lorem ipsum " * 1000
    assert count_tokens(long) > 100
