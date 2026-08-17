"""CodeDrift: synthetic weatherlib API drift generator for LTCF-Bench E1.

Simulates three versions of a weatherlib API that drifts over time.
Each version has a set of tasks (API calls) with correct answers per version.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiVersion:
    name: str
    version: int
    correct_call: str
    deprecated_calls: tuple[str, ...]
    description: str


# Three drifting versions of weatherlib
VERSIONS = [
    ApiVersion(
        name="weatherlib_v1",
        version=1,
        correct_call="weatherlib.get_weather(city)",
        deprecated_calls=(),
        description="Original API: get_weather(city) is the main entry point.",
    ),
    ApiVersion(
        name="weatherlib_v2",
        version=2,
        correct_call="weatherlib.fetch_forecast(city)",
        deprecated_calls=("weatherlib.get_weather(city)",),
        description="v2 API: get_weather() removed. Use fetch_forecast(city) instead.",
    ),
    ApiVersion(
        name="weatherlib_v3",
        version=3,
        correct_call="weatherlib.WeatherClient().fetch(city)",
        deprecated_calls=(
            "weatherlib.get_weather(city)",
            "weatherlib.fetch_forecast(city)",
        ),
        description="v3 API: OOP refactor. Use WeatherClient().fetch(city).",
    ),
]

VERSION_MAP: dict[int, ApiVersion] = {v.version: v for v in VERSIONS}


@dataclass(frozen=True)
class Task:
    """A single task: agent must pick the correct API call for a given library version."""
    task_id: str
    question: str
    correct_answer: str
    api_version: int


def generate_tasks(n_per_version: int = 10) -> list[Task]:
    """Generate n_per_version tasks for each of the 3 API versions."""
    tasks: list[Task] = []
    for ver in VERSIONS:
        for i in range(n_per_version):
            tasks.append(Task(
                task_id=f"v{ver.version}_t{i:02d}",
                question=f"How do I fetch weather for 'London' using weatherlib? (task {i})",
                correct_answer=ver.correct_call,
                api_version=ver.version,
            ))
    return tasks


def get_observations_for_version(version: int) -> list[str]:
    """Return the noisy observations an agent sees when weatherlib drifts.

    Deliberately includes noise: deprecated calls appear in error messages,
    old API is mentioned in migration notes. A naive retriever gets confused;
    a consolidation-based system extracts the clean rule.
    """
    obs: list[str] = []
    if version == 2:
        obs = [
            # Noise: old API mentioned in error context
            "ERROR: weatherlib.get_weather(city) failed -- AttributeError in v2",
            # Noise: migration note mentions both old and new
            "Migration note: stop calling weatherlib.get_weather(city), switch to fetch_forecast",
            # Positive signal (implicit, no exact signature)
            "weatherlib v2 introduces fetch_forecast as the new primary API",
            # Positive signal (explicit)
            "weatherlib.fetch_forecast(city) confirmed working in v2 integration test",
            # Noise: confusing statement about old API being faster (misleading positive mention)
            "Note: weatherlib.get_weather(city) was faster but is now unavailable in v2",
        ]
    elif version == 3:
        obs = [
            # Noise: both deprecated calls appear in errors
            "ERROR: weatherlib.fetch_forecast(city) no longer exists in v3",
            "ERROR: weatherlib.get_weather(city) still broken in v3 as expected",
            # Noise: intermediate references
            "v3 dropped both get_weather and fetch_forecast in favour of OOP design",
            # Positive signal (implicit)
            "weatherlib v3 exposes a WeatherClient class with a fetch method",
            # Positive signal (explicit)
            "weatherlib.WeatherClient().fetch(city) is the v3 recommended entry point",
        ]
    return obs
