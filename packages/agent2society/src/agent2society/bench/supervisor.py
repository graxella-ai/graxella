"""Native A2A supervisor baseline.

This is the *honest* baseline agent2society is compared against. It is the
standard pattern users would write today: a supervisor LLM that:

  1. Receives a system prompt listing every agent and its skills.
  2. Receives the user task.
  3. Returns a JSON decision identifying the chosen agent and skill.
  4. The host then dispatches to that agent over A2A.

The system prompt is the realistic shape — token cost scales linearly
with mesh size. That's the cost agent2society avoids by precomputing skill
vectors and matching them against one embedding of the task.

Two `chat_fn` modes are supported:

  * **None (default)** — a deterministic mock that picks the same agent
    agent2society would pick. This equalises *correctness* between the two
    methods so the headline number purely measures coordination cost.
    No API keys required.
  * **Real callable** — `chat_fn(messages: list[dict]) -> dict`. The dict
    must contain at least `{"content": "..."}`; if the provider returns
    usage data, include `{"usage": {"prompt_tokens": int,
    "completion_tokens": int}}` and the harness will trust it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..conformance import check as conformance_check
from ..dispatcher import (
    CompositeTransport,
    HttpTransport,
    LocalTransport,
    build_message_send_payload,
    extract_text,
)
from ..graph import CapabilityGraph
from ..router import Router
from .tokens import TokenFn, count_tokens


SUPERVISOR_SYSTEM = (
    "You are an agent router for a multi-agent mesh. You will be given a "
    "user task and a list of available agents with their declared skills. "
    "Decide which single agent and skill should handle this task. Respond "
    "with JSON ONLY in the form:\n"
    '{"agent": "<agent-name>", "skill": "<skill-id>"}\n'
    "Do not include any other text. Pick the best match; if no agent fits, "
    'return {"agent": null, "skill": null}.\n'
    "\nAvailable agents:\n"
)


def render_mesh_for_supervisor(graph: CapabilityGraph) -> str:
    """Build the supervisor's mesh listing — realistic, not strawman."""
    blocks: List[str] = []
    for i, node in enumerate(graph.agents(), 1):
        lines = [f"{i}. {node.card.name}"]
        if node.card.description:
            lines.append(f"   description: {node.card.description}")
        lines.append("   skills:")
        for s in node.card.skills:
            lines.append(f"     - {s.id}: {s.description or s.name}")
            if s.tags:
                lines.append(f"       tags: {', '.join(s.tags)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_supervisor_prompt(graph: CapabilityGraph, task: str) -> List[Dict[str, str]]:
    system = SUPERVISOR_SYSTEM + render_mesh_for_supervisor(graph)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Task: {task}"},
    ]


# ---- chat_fn protocol ------------------------------------------------

ChatFn = Callable[[List[Dict[str, str]]], Dict[str, Any]]


@dataclass
class ChatResult:
    content: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _coerce_chat_result(
    raw: Dict[str, Any],
    messages: List[Dict[str, str]],
    token_fn: TokenFn,
) -> ChatResult:
    """Trust provider usage if present, otherwise count locally."""
    content = str(raw.get("content", ""))
    usage = raw.get("usage") or {}
    if "prompt_tokens" in usage and "completion_tokens" in usage:
        return ChatResult(
            content=content,
            prompt_tokens=int(usage["prompt_tokens"]),
            completion_tokens=int(usage["completion_tokens"]),
        )
    prompt_tokens = sum(token_fn(m.get("content", "")) for m in messages)
    completion_tokens = token_fn(content)
    return ChatResult(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def make_mock_chat_fn(graph: CapabilityGraph) -> ChatFn:
    """Default mock: picks the agent agent2society would pick.

    This equalises correctness between supervisor and agent2society so that the
    delta in coordination tokens is the only thing being measured. It is
    NOT a claim that real LLMs always pick correctly — it's a deliberate
    choice so the benchmark headline is "cost, holding correctness equal".
    """
    router = Router(graph)

    def _fn(messages: List[Dict[str, str]]) -> Dict[str, Any]:
        task = ""
        for m in messages:
            if m.get("role") == "user":
                # User message is "Task: <task>"
                content = m.get("content", "")
                if content.startswith("Task:"):
                    task = content[len("Task:") :].strip()
                else:
                    task = content
        cands = router.route(task, top_k=1)
        if not cands:
            payload = {"agent": None, "skill": None}
        else:
            top = cands[0]
            payload = {"agent": top.agent, "skill": top.skill_id}
        return {"content": json.dumps(payload)}

    return _fn


# ---- decision parsing -------------------------------------------------

_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def parse_supervisor_decision(text: str) -> Dict[str, Optional[str]]:
    """Extract {agent, skill} from a supervisor's response, tolerantly."""
    text = (text or "").strip()
    # Strip markdown fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    candidates = [text]
    candidates.extend(_JSON_RE.findall(text))
    for c in candidates:
        try:
            data = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return {
                "agent": data.get("agent"),
                "skill": data.get("skill") or data.get("skill_id"),
            }
    return {"agent": None, "skill": None}


# ---- baseline runner --------------------------------------------------

@dataclass
class SupervisorRun:
    task: str
    chosen_agent: Optional[str]
    chosen_skill: Optional[str]
    coord_prompt_tokens: int
    coord_completion_tokens: int
    dispatched: bool
    response_text: str = ""
    conformance_ok: bool = True
    conformance_reason: Optional[str] = None
    error: Optional[str] = None

    @property
    def coordination_tokens(self) -> int:
        return self.coord_prompt_tokens + self.coord_completion_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "chosen_agent": self.chosen_agent,
            "chosen_skill": self.chosen_skill,
            "coordination_tokens": self.coordination_tokens,
            "coord_prompt_tokens": self.coord_prompt_tokens,
            "coord_completion_tokens": self.coord_completion_tokens,
            "dispatched": self.dispatched,
            "conformance_ok": self.conformance_ok,
            "conformance_reason": self.conformance_reason,
            "error": self.error,
        }


class SupervisorBaseline:
    """Run tasks through a native A2A supervisor pattern.

    Shares the dispatcher / local-transport infrastructure with `Mesh` so
    dispatch cost is held equal between the two methods.
    """

    def __init__(
        self,
        graph: CapabilityGraph,
        *,
        chat_fn: Optional[ChatFn] = None,
        local_transport: Optional[LocalTransport] = None,
        token_fn: Optional[TokenFn] = None,
        enforce_conformance: bool = False,
    ) -> None:
        self.graph = graph
        self._chat_fn = chat_fn or make_mock_chat_fn(graph)
        self._token_fn = token_fn or count_tokens
        self._local = local_transport or LocalTransport()
        self._transport = CompositeTransport(self._local, HttpTransport())
        # By default the baseline does NOT run a conformance check — that's
        # the whole point: real supervisors don't, which is why bad handoffs
        # happen. Set True for an ablation that adds the guardrail to the
        # baseline as well.
        self._enforce = enforce_conformance

    def run(self, task: str) -> SupervisorRun:
        messages = build_supervisor_prompt(self.graph, task)
        try:
            raw = self._chat_fn(messages)
        except Exception as e:
            return SupervisorRun(
                task=task,
                chosen_agent=None,
                chosen_skill=None,
                coord_prompt_tokens=sum(
                    self._token_fn(m.get("content", "")) for m in messages
                ),
                coord_completion_tokens=0,
                dispatched=False,
                error=f"chat_fn error: {e}",
            )
        result = _coerce_chat_result(raw, messages, self._token_fn)
        decision = parse_supervisor_decision(result.content)

        run = SupervisorRun(
            task=task,
            chosen_agent=decision.get("agent"),
            chosen_skill=decision.get("skill"),
            coord_prompt_tokens=result.prompt_tokens,
            coord_completion_tokens=result.completion_tokens,
            dispatched=False,
        )

        if not run.chosen_agent or not run.chosen_skill:
            run.error = "supervisor returned no decision"
            return run

        node = self.graph.get(run.chosen_agent)
        if node is None:
            run.error = f"supervisor named unknown agent {run.chosen_agent!r}"
            return run

        if self._enforce:
            res = conformance_check(
                self.graph,
                agent=run.chosen_agent,
                skill_id=run.chosen_skill,
                task=task,
            )
            run.conformance_ok = res.ok
            run.conformance_reason = res.reason
            if not res.ok:
                return run

        payload = build_message_send_payload(task=task, skill_id=run.chosen_skill)
        try:
            response = self._transport.send(node.url, payload)
        except Exception as e:
            run.error = f"dispatch error: {e}"
            return run
        run.response_text = extract_text(response)
        run.dispatched = True
        return run
