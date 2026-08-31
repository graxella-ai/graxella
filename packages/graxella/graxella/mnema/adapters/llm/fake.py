from __future__ import annotations

from graxella.mnema.ports.llm import ContradictionProposal, RuleProposal, SkillProposal


class FakeLLM:
    """Deterministic LLM adapter for testing. No network calls."""

    def __init__(
        self,
        *,
        rules: list[dict] | None = None,
        skills: list[dict] | None = None,
    ) -> None:
        self._preset_rules = rules
        self._preset_skills = skills
        self.rule_calls: int = 0
        self.skill_calls: int = 0

    def extract_rules(
        self,
        assertions: list[dict],
        *,
        max_rules: int = 10,
    ) -> list[RuleProposal]:
        """Return deterministic rule proposals. No network calls."""
        self.rule_calls += 1

        scenario = getattr(self, "_scenario", None)

        if scenario == "weatherlib_v2":
            return [
                RuleProposal(
                    text="Use weatherlib.fetch_forecast(city) -- get_weather() removed in v2.",
                    scope="weatherlib",
                    confidence=0.95,
                    derived_from=tuple(a["id"] for a in assertions[:2]) or ("placeholder",),
                )
            ]
        if scenario == "weatherlib_v3":
            return [
                RuleProposal(
                    text=(
                        "Use weatherlib.WeatherClient().fetch(city) "
                        "-- fetch_forecast() removed in v3."
                    ),
                    scope="weatherlib",
                    confidence=0.95,
                    derived_from=tuple(a["id"] for a in assertions[:2]) or ("placeholder",),
                )
            ]
        if scenario == "empty":
            return []

        if self._preset_rules is not None:
            return [RuleProposal(**r) for r in self._preset_rules[:max_rules]]

        # Group by subject, produce one rule per group.
        groups: dict[str, list[dict]] = {}
        for a in assertions:
            key = a.get("subject") or "general"
            groups.setdefault(key, []).append(a)

        results: list[RuleProposal] = []
        for subject, group in groups.items():
            statement = group[0].get("statement", "")
            text = statement[:80]
            confidences = [a.get("confidence", 0.8) for a in group]
            avg_conf = sum(confidences) / len(confidences)
            ids = tuple(a["id"] for a in group)
            results.append(
                RuleProposal(
                    text=text if text else "No statement.",
                    scope=subject,
                    confidence=avg_conf,
                    derived_from=ids,
                )
            )
            if len(results) >= max_rules:
                break

        return results

    def extract_skills(
        self,
        assertions: list[dict],
        *,
        max_skills: int = 5,
    ) -> list[SkillProposal]:
        """Return deterministic skill proposals. No network calls."""
        self.skill_calls += 1

        scenario = getattr(self, "_scenario", None)

        if scenario == "weatherlib_v2":
            return [
                SkillProposal(
                    name="fetch_weather",
                    description="Fetch weather via weatherlib v2",
                    code=(
                        "def fetch_weather(city: str) -> dict:\n"
                        "    import weatherlib\n"
                        "    return weatherlib.fetch_forecast(city)\n"
                    ),
                    signature="def fetch_weather(city: str) -> dict",
                    derived_from=tuple(a["id"] for a in assertions[:1]) or ("placeholder",),
                )
            ]
        if scenario == "weatherlib_v3":
            return [
                SkillProposal(
                    name="fetch_weather",
                    description="Fetch weather via weatherlib v3",
                    code=(
                        "def fetch_weather(city: str) -> dict:\n"
                        "    import weatherlib\n"
                        "    return weatherlib.WeatherClient().fetch(city)\n"
                    ),
                    signature="def fetch_weather(city: str) -> dict",
                    derived_from=tuple(a["id"] for a in assertions[:1]) or ("placeholder",),
                )
            ]
        if scenario == "empty":
            return []

        if self._preset_skills is not None:
            return [SkillProposal(**s) for s in self._preset_skills[:max_skills]]

        return []

    def detect_contradictions(
        self,
        assertions: list[dict],
    ) -> list[ContradictionProposal]:
        """Return deterministic contradiction proposals for test scenarios."""
        self.contradiction_calls = getattr(self, "contradiction_calls", 0) + 1
        scenario = getattr(self, "_scenario", None)

        if scenario == "contradicts_rate_limit":
            # Find the two assertions whose statements mention "rpm" or "rate"
            rate_ids = [
                a["id"] for a in assertions
                if any(kw in a.get("statement", "").lower() for kw in ("rpm", "rate limit"))
            ]
            if len(rate_ids) >= 2:
                return [
                    ContradictionProposal(
                        from_id=rate_ids[0],
                        to_id=rate_ids[1],
                        reason="Two conflicting rate limit values: 100 rpm vs 60 rpm.",
                        confidence=0.95,
                    )
                ]
        return []

    def answer_question(self, context: str, question: str) -> str:
        """Deterministic answer extraction for tests — keyword overlap scoring."""
        if not context:
            return "unknown"
        question_words = set(question.lower().split())
        best_line, best_score = "", 0
        for line in context.splitlines():
            score = sum(1 for w in question_words if w in line.lower())
            if score > best_score:
                best_score = score
                best_line = line.strip("- ").strip()
        return best_line or "unknown"

    @classmethod
    def from_scenario(cls, scenario: str) -> FakeLLM:
        """Construct a FakeLLM configured for a named test scenario."""
        obj = cls()
        obj._scenario = scenario
        return obj
