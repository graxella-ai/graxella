from __future__ import annotations

import agent2society as r2r


def _mesh(*cards):
    m = r2r.Mesh()
    for c in cards:
        m.add(c)
    return m


def test_route_picks_writer_for_memo(research_card, writer_card):
    m = _mesh(research_card, writer_card)
    cands = m.route("Draft an executive memo on customer churn")
    assert cands, "expected at least one candidate"
    assert cands[0].agent == "writer-agent"
    assert cands[0].skill_id == "exec_memo"


def test_route_picks_researcher_for_research(research_card, writer_card):
    m = _mesh(research_card, writer_card)
    cands = m.route("Research Q3 churn drivers from the web")
    assert cands[0].agent == "research-agent"
    assert cands[0].skill_id == "web_research"


def test_route_picks_coder_for_code(research_card, writer_card, coder_card):
    m = _mesh(research_card, writer_card, coder_card)
    cands = m.route("Write a Python function to dedupe a list")
    assert cands[0].agent == "coder-agent"


def test_empty_mesh_returns_no_candidates():
    m = r2r.Mesh()
    assert m.route("anything") == []


def test_custom_embed_fn_is_used(research_card, writer_card):
    seen = []

    def fake_embed(texts):
        # Pin every input — corpus or query — to the writer's axis.
        # The router uses cosine similarity over these vectors, so this
        # forces the writer to win regardless of the input text.
        seen.append(list(texts))
        out = []
        for t in texts:
            if "exec_memo" in t or "Executive" in t or "Memo" in t:
                out.append([1.0, 0.0])
            elif "web_research" in t or "Web Research" in t:
                out.append([0.0, 1.0])
            else:
                # Query falls here — align it with the writer.
                out.append([1.0, 0.0])
        return out

    m = r2r.Mesh(embed_fn=fake_embed)
    m.add(research_card)
    m.add(writer_card)
    cands = m.route("anything at all")
    assert seen, "embed_fn was never called"
    assert cands[0].agent == "writer-agent"
