"""Observation-driven routing improvement.

v1 agent2society routes deterministically. When the default TF-IDF scorer picks
the wrong skill on a lexically ambiguous task, the documented v1 fix is to
swap in a real embedder via `Society(embed_fn=...)`. v0.4 adds a second
lever that does not require a model at all: **mine discriminative tokens
from labeled past decisions and propose them as skill tags**.

The optimizer is deterministic and detection-only:

  1. Read past routing explanations (already stored on the society).
  2. For each labeled `(handoff_id, correct_agent, correct_skill)` triple
     where the past decision was wrong, find task tokens that appear in
     the missed task(s) but not in the correct skill's text, and that
     are not too generic across the rest of the mesh.
  3. For each candidate change, **backtest** it against a *snapshot copy*
     of the graph -- the live graph is never touched during backtest.
  4. Return a `OptimizationReport`. The caller decides whether to apply.

The live graph is only mutated by `apply_optimization`, not by `optimize`.
The backtest in step 3 clones the relevant graph state into an isolated
snapshot, so concurrent `Society.run()` calls see no change during
optimization.

Public surface (mirrored on `Society`):

    report = society.optimize(labels)
    print(report.render())          # nothing has changed yet
    society.apply_optimization(report)   # explicit second step
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import (
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from .card import AgentCard, Skill
from .embeddings import EmbedFn, tokenize
from .explanation import RoutingExplanation
from .graph import CapabilityGraph
from .router import Router


# Tiny stopword/generic-token list. Kept short on purpose -- the goal is
# not full NLP, only to avoid the most obvious "and"/"the" noise tokens
# from leaking into skill tags.
_GENERIC_TOKENS = frozenset(
    {
        "the", "a", "an", "of", "to", "for", "on", "in", "at", "by", "with",
        "from", "and", "or", "but", "is", "are", "be", "this", "that",
        "these", "those", "into", "over", "under", "through", "across",
        "about", "we", "they", "you", "i", "it", "my", "our", "your",
        "their", "do", "does", "did", "done", "task", "sub", "step",
        "please", "kindly", "given", "produce", "based", "out", "one",
        "two", "three", "any", "all", "some", "each", "every",
    }
)


# A label points at a stored explanation and asserts the correct route.
Label = Tuple[str, str, str]  # (handoff_id, correct_agent, correct_skill)

# Optional LLM proposer for v0.5. Given the missed tasks for one skill plus
# the agent card and target skill, return additional tag candidates to
# consider. The LLM only *proposes* -- its suggestions are filtered and
# backtested exactly like the discriminative tokens, so the LLM never
# decides routing.
LLMSuggestFn = Callable[
    [Sequence[str], AgentCard, Skill],
    Sequence[str],
]


@dataclass(frozen=True)
class SkillEdit:
    """A proposed change to a single skill's tags.

    `fixes` is the number of labeled misses this edit corrected during
    backtest. `regressions` is the number of previously-correct labels
    it broke. Net wins == fixes - regressions; only edits with net wins
    above zero are accepted by `apply_optimization`.
    """

    agent: str
    skill_id: str
    add_tags: Tuple[str, ...]
    reason: str
    fixes: int = 0
    regressions: int = 0

    @property
    def net_wins(self) -> int:
        return self.fixes - self.regressions

    @property
    def accepted(self) -> bool:
        return self.net_wins > 0


@dataclass(frozen=True)
class OptimizationReport:
    """Result of `society.optimize(labels)`.

    Read-only. `society.apply_optimization(report)` is the explicit
    second step -- nothing on the society changes until you call it.
    """

    edits: Tuple[SkillEdit, ...]
    labels_seen: int
    labels_missing_explanations: int
    labels_already_correct: int
    misses_addressed: int

    @property
    def accepted_edits(self) -> Tuple[SkillEdit, ...]:
        return tuple(e for e in self.edits if e.accepted)

    @property
    def rejected_edits(self) -> Tuple[SkillEdit, ...]:
        return tuple(e for e in self.edits if not e.accepted)

    def render(self) -> str:
        lines: List[str] = []
        lines.append("agent2society optimization report")
        lines.append("=" * 48)
        lines.append(f"labels seen:            {self.labels_seen}")
        if self.labels_missing_explanations:
            lines.append(
                f"  missing explanations: {self.labels_missing_explanations} "
                f"(handoff ids not in this society's ledger)"
            )
        lines.append(f"already correct:        {self.labels_already_correct}")
        lines.append(f"misses to address:      {self.misses_addressed}")
        lines.append(f"edits proposed:         {len(self.edits)}")
        lines.append(f"  accepted (net win):   {len(self.accepted_edits)}")
        lines.append(f"  rejected (regression):{len(self.rejected_edits)}")
        lines.append("")
        if not self.edits:
            lines.append(
                "  (no edits proposed -- routing already matched all labels, "
                "or no discriminating tokens were found)"
            )
            return "\n".join(lines)
        for e in self.edits:
            status = "ACCEPT" if e.accepted else "REJECT"
            lines.append(
                f"  [{status}] {e.agent}::{e.skill_id}  +tags={list(e.add_tags)}"
            )
            lines.append(
                f"           fixes={e.fixes}  regressions={e.regressions}  "
                f"net={e.net_wins:+d}"
            )
            lines.append(f"           reason: {e.reason}")
        return "\n".join(lines)


def optimize(
    *,
    graph: CapabilityGraph,
    embed_fn: Optional[EmbedFn],
    explanations: Mapping[str, RoutingExplanation],
    labels: Sequence[Label],
    max_tags_per_skill: int = 3,
    generic_token_threshold: float = 0.5,
    llm_fn: Optional[LLMSuggestFn] = None,
) -> OptimizationReport:
    """Mine discriminative tokens from labeled misses, backtest, report.

    The live graph is never mutated. All backtest routing runs against an
    isolated snapshot built from `deepcopy(graph)`. Parameters:

      graph                       the society's capability graph (read-only)
      embed_fn                    the embed function used by the live router
                                  (used to build consistent snapshot routers)
      explanations                the society's stored RoutingExplanations
      labels                      list of (handoff_id, agent, skill) triples
      max_tags_per_skill          cap on how many new tags one edit may add
      generic_token_threshold     skip tokens that appear in more than this
                                  fraction of other skill texts
      llm_fn                      optional proposer; suggestions are filtered
                                  and backtested like discriminative tokens
    """
    label_list = list(labels)

    # Step 1: triage labels into already-correct, miss-with-known-explanation,
    # and missing-explanation.
    misses_by_skill: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    backtest_set: List[Tuple[str, str, str, str]] = []  # (hid, task, c_agent, c_skill)
    missing = 0
    already_correct = 0
    for hid, c_agent, c_skill in label_list:
        exp = explanations.get(hid)
        if exp is None:
            missing += 1
            continue
        backtest_set.append((hid, exp.task, c_agent, c_skill))
        if exp.chosen_agent == c_agent and exp.chosen_skill == c_skill:
            already_correct += 1
            continue
        misses_by_skill.setdefault((c_agent, c_skill), []).append((hid, exp.task))

    if not misses_by_skill:
        return OptimizationReport(
            edits=(),
            labels_seen=len(label_list),
            labels_missing_explanations=missing,
            labels_already_correct=already_correct,
            misses_addressed=0,
        )

    # Step 2: per missed skill, mine candidate tokens to propose as new tags.
    proposals: List[Tuple[str, str, Tuple[str, ...], str]] = []
    for (c_agent, c_skill), missed in misses_by_skill.items():
        candidates = _mine_candidates(
            graph,
            target_agent=c_agent,
            target_skill=c_skill,
            missed_tasks=[t for _, t in missed],
            max_tokens=max_tags_per_skill,
            generic_threshold=generic_token_threshold,
            llm_fn=llm_fn,
        )
        if not candidates:
            continue
        chosen_tokens, reason = candidates
        proposals.append((c_agent, c_skill, chosen_tokens, reason))

    if not proposals:
        return OptimizationReport(
            edits=(),
            labels_seen=len(label_list),
            labels_missing_explanations=missing,
            labels_already_correct=already_correct,
            misses_addressed=sum(len(m) for m in misses_by_skill.values()),
        )

    # Step 3: build a baseline snapshot once. The baseline router routes
    # the backtest tasks with the *current* (unmodified) skill tags.
    # Each proposal then builds its own "after" snapshot with the proposed
    # edit applied. Neither snapshot touches the live graph.
    baseline_graph = deepcopy(graph)
    baseline_router = Router(baseline_graph, embed_fn=embed_fn)
    baseline_router._rebuild_index()

    baseline_decisions: Dict[str, Optional[Tuple[str, str]]] = {}
    for hid, task, _, _ in backtest_set:
        cands = baseline_router.route(task, top_k=5)
        top = next(iter(cands), None)
        baseline_decisions[hid] = (top.agent, top.skill_id) if top else None

    # Step 4: per proposal, build an "after" snapshot and count fixes/regressions.
    edits: List[SkillEdit] = []
    for c_agent, c_skill, tokens, reason in proposals:
        fixes, regressions = _backtest_edit(
            baseline_graph=baseline_graph,
            baseline_decisions=baseline_decisions,
            embed_fn=embed_fn,
            backtest_set=backtest_set,
            agent=c_agent,
            skill_id=c_skill,
            add_tags=tokens,
        )
        edits.append(
            SkillEdit(
                agent=c_agent,
                skill_id=c_skill,
                add_tags=tokens,
                reason=reason,
                fixes=fixes,
                regressions=regressions,
            )
        )

    return OptimizationReport(
        edits=tuple(edits),
        labels_seen=len(label_list),
        labels_missing_explanations=missing,
        labels_already_correct=already_correct,
        misses_addressed=sum(len(m) for m in misses_by_skill.values()),
    )


def apply_optimization(
    graph: CapabilityGraph,
    router: Router,
    report: OptimizationReport,
) -> int:
    """Apply only the report's accepted (net-win) edits to the given graph.

    Called by `Mesh.apply_optimization` which wraps this in a CoW swap so
    the live routing graph is replaced atomically rather than mutated in
    place. The router is marked stale so the first `route()` call rebuilds.
    """
    applied = 0
    for edit in report.accepted_edits:
        node = graph.get(edit.agent)
        if node is None:
            continue
        idx = _skill_index(node.card, edit.skill_id)
        if idx is None:
            continue
        original = node.card.skills[idx]
        merged = list(original.tags) + [
            t for t in edit.add_tags if t not in original.tags
        ]
        node.card.skills[idx] = replace(original, tags=merged)
        applied += 1
    if applied:
        router.mark_stale()
    return applied


# ---- internals -------------------------------------------------------------


def _skill_index(card: "AgentCard", skill_id: str) -> Optional[int]:
    for i, s in enumerate(card.skills):
        if s.id == skill_id:
            return i
    return None


def _mine_candidates(
    graph: CapabilityGraph,
    *,
    target_agent: str,
    target_skill: str,
    missed_tasks: Sequence[str],
    max_tokens: int,
    generic_threshold: float,
    llm_fn: Optional[LLMSuggestFn] = None,
) -> Optional[Tuple[Tuple[str, ...], str]]:
    """Pick up to `max_tokens` discriminative tokens for one skill.

    When `llm_fn` is provided, its suggestions are added to the candidate
    pool alongside discriminative tokens mined from missed tasks. LLM
    suggestions go through the same filters (existing, generic, length).
    """
    node = graph.get(target_agent)
    if node is None:
        return None
    skill = node.card.skill(target_skill)
    if skill is None:
        return None

    existing = set(tokenize(skill.search_text())) | set(tokenize(node.card.description))

    other_skill_count: Dict[str, int] = {}
    n_other = 0
    for other_node in graph.agents():
        for other_skill in other_node.card.skills:
            if other_node.name == target_agent and other_skill.id == target_skill:
                continue
            n_other += 1
            text = " ".join([other_node.card.description, other_skill.search_text()])
            for t in set(tokenize(text)):
                other_skill_count[t] = other_skill_count.get(t, 0) + 1

    miss_count: Dict[str, int] = {}
    n_missed = max(len(missed_tasks), 1)
    for task in missed_tasks:
        for t in set(tokenize(task)):
            miss_count[t] = miss_count.get(t, 0) + 1

    llm_tokens: Set[str] = set()
    if llm_fn is not None:
        try:
            suggested = llm_fn(list(missed_tasks), node.card, skill)
        except Exception:
            suggested = []
        for raw in suggested or []:
            for tok in tokenize(str(raw)):
                llm_tokens.add(tok)

    scored: List[Tuple[str, int, bool]] = []
    for tok, count in miss_count.items():
        if not _passes_filters(tok, existing, other_skill_count, n_other, generic_threshold):
            continue
        scored.append((tok, count, False))
    for tok in llm_tokens:
        if tok in miss_count:
            continue
        if not _passes_filters(tok, existing, other_skill_count, n_other, generic_threshold):
            continue
        scored.append((tok, 1, True))

    if not scored:
        return None

    scored.sort(key=lambda kv: (-kv[1], kv[2], kv[0]))
    chosen = tuple(t for t, _, _ in scored[:max_tokens])
    from_llm = any(t[2] for t in scored[:max_tokens])
    reason = (
        f"tokens {list(chosen)} appear in missed task(s) "
        f"({n_missed} miss(es)) but not in this skill's existing text, "
        f"and are not generic across the rest of the mesh"
    )
    if from_llm:
        reason += " (some candidates contributed by llm_fn)"
    return chosen, reason


def _passes_filters(
    tok: str,
    existing: Set[str],
    other_skill_count: Dict[str, int],
    n_other: int,
    generic_threshold: float,
) -> bool:
    if tok in existing:
        return False
    if tok in _GENERIC_TOKENS:
        return False
    if len(tok) <= 2:
        return False
    if n_other > 0 and other_skill_count.get(tok, 0) / n_other > generic_threshold:
        return False
    return True


def _backtest_edit(
    *,
    baseline_graph: CapabilityGraph,
    baseline_decisions: Dict[str, Optional[Tuple[str, str]]],
    embed_fn: Optional[EmbedFn],
    backtest_set: Sequence[Tuple[str, str, str, str]],
    agent: str,
    skill_id: str,
    add_tags: Sequence[str],
) -> Tuple[int, int]:
    """Count fixes vs regressions for one proposed edit.

    Works entirely on snapshots -- the live graph is never touched.

    `baseline_decisions` is pre-computed: what the baseline router would
    pick for each backtest task. We build a single `after` snapshot (the
    baseline graph with the proposed edit applied) and route all tasks
    against it. No mutation-and-revert loop.
    """
    node = baseline_graph.get(agent)
    if node is None:
        return 0, 0
    idx = _skill_index(node.card, skill_id)
    if idx is None:
        return 0, 0

    original = node.card.skills[idx]
    merged_tags = list(original.tags) + [t for t in add_tags if t not in original.tags]
    if merged_tags == list(original.tags):
        return 0, 0

    # Build "after" snapshot: deepcopy the baseline, apply the edit.
    after_graph = deepcopy(baseline_graph)
    after_node = after_graph.get(agent)
    after_idx = _skill_index(after_node.card, skill_id)
    after_node.card.skills[after_idx] = replace(original, tags=merged_tags)

    after_router = Router(after_graph, embed_fn=embed_fn)
    after_router._rebuild_index()

    fixes = 0
    regressions = 0
    for hid, task, c_agent, c_skill in backtest_set:
        before_choice = baseline_decisions.get(hid)
        cands = after_router.route(task, top_k=5)
        top = next(iter(cands), None)
        after_choice = (top.agent, top.skill_id) if top else None

        was_correct = before_choice == (c_agent, c_skill)
        now_correct = after_choice == (c_agent, c_skill)
        if not was_correct and now_correct:
            fixes += 1
        elif was_correct and not now_correct:
            regressions += 1

    return fixes, regressions
