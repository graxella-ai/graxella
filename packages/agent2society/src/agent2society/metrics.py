"""Production-grade metrics for the routing layer.

A `MetricsCollector` accumulates counters and observations and exposes
them in two forms:

  * `snapshot()` -- a plain dict, easy to assert in tests and ship to a
    JSON endpoint.
  * `render_prometheus()` -- text in the Prometheus exposition format,
    which is what every scrape-pull stack (Prometheus, VictoriaMetrics,
    OpenMetrics) understands without an extra dependency.

The collector is thread-safe. The hot path (`inc`, `observe`) takes a
single lock so concurrent `Society.run()` calls do not corrupt counts.

The collector itself never blocks routing -- exceptions inside record
calls are swallowed, mirroring the governance hook safety policy. A
broken telemetry pipe must not be able to take the dispatch path down.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

# A label set is a frozen tuple of (key, value) pairs. Tuples are hashable
# so they key our counters dict directly without needing a wrapper.
LabelSet = Tuple[Tuple[str, str], ...]


def _labels(labels: Optional[Dict[str, str]]) -> LabelSet:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


@dataclass
class _Histogram:
    """Summary-style histogram: count + sum + min + max.

    This is enough to compute rate, average, p-min/p-max in Prometheus or
    Grafana. We deliberately do not implement bucketed quantiles -- they
    add cost and the user can layer them on top with a real Prometheus
    client if they need them.
    """

    count: int = 0
    total: float = 0.0
    minimum: float = float("inf")
    maximum: float = float("-inf")

    def observe(self, v: float) -> None:
        self.count += 1
        self.total += v
        if v < self.minimum:
            self.minimum = v
        if v > self.maximum:
            self.maximum = v

    def as_dict(self) -> Dict[str, float]:
        if self.count == 0:
            return {"count": 0, "sum": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
        return {
            "count": self.count,
            "sum": round(self.total, 6),
            "min": round(self.minimum, 6),
            "max": round(self.maximum, 6),
            "avg": round(self.total / self.count, 6),
        }


# Counter / histogram names registered up-front so the snapshot is stable
# even when no events have fired yet (scrapers prefer that).
_COUNTERS: FrozenSet[str] = frozenset(
    {
        "agent2society_routes_total",
        "agent2society_dispatches_total",
        "agent2society_dispatch_retries_total",
        "agent2society_dispatch_failures_total",
        "agent2society_conformance_blocked_total",
        "agent2society_unroutable_total",
        "agent2society_conflicts_detected_total",
        "agent2society_low_confidence_total",
        "agent2society_drift_detected_total",
        "agent2society_human_review_total",
        "agent2society_optimizer_edits_applied_total",
        "agent2society_low_margin_total",
        "agent2society_ood_total",
        "agent2society_vector_ambiguity_total",
    }
)
_HISTOGRAMS: FrozenSet[str] = frozenset(
    {
        "agent2society_route_score",
        "agent2society_request_tokens",
        "agent2society_response_tokens",
    }
)


# Short help strings rendered in the Prometheus output. The strings are
# kept terse on purpose -- they have to fit on one line in the exposition.
_HELP: Dict[str, str] = {
    "agent2society_routes_total": "Routing decisions emitted by Society.run().",
    "agent2society_dispatches_total": "Successful transport dispatches.",
    "agent2society_dispatch_retries_total": "Dispatches that fell over to a fallback candidate after a transport error.",
    "agent2society_dispatch_failures_total": "Dispatches that ultimately failed even after retry.",
    "agent2society_conformance_blocked_total": "Routes blocked because every above-threshold candidate failed conformance.",
    "agent2society_unroutable_total": "Routes that produced no candidate above min_score.",
    "agent2society_conflicts_detected_total": "Conflicts surfaced by ConflictDetector.",
    "agent2society_low_confidence_total": "on_low_confidence hook firings.",
    "agent2society_drift_detected_total": "Capability drift events surfaced.",
    "agent2society_human_review_total": "on_human_review hook firings.",
    "agent2society_optimizer_edits_applied_total": "Optimizer edits successfully applied.",
    "agent2society_low_margin_total": "Routes where top-1 and top-2 score gap was below the low_margin_threshold.",
    "agent2society_ood_total": "Routes with no candidate above min_score (out-of-domain).",
    "agent2society_vector_ambiguity_total": "Routes where top-3 candidates fell within the ambiguity band.",
    "agent2society_route_score": "Score of the chosen candidate.",
    "agent2society_request_tokens": "Estimated request tokens per dispatch.",
    "agent2society_response_tokens": "Estimated response tokens per dispatch.",
}


class MetricsCollector:
    """Thread-safe counter + histogram registry.

    Public surface:

      collector.inc(name, labels=..., by=1)
      collector.observe(name, value, labels=...)
      collector.snapshot()         -> dict
      collector.render_prometheus()-> str
      collector.reset()            -> wipe all data (mostly for tests)

    Unknown metric names are accepted (no schema enforcement); this lets
    the optimizer or a user-supplied integration add custom series
    without monkey-patching this module. Known names get richer help
    strings in the Prometheus output.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[Tuple[str, LabelSet], int] = {}
        self._histograms: Dict[Tuple[str, LabelSet], _Histogram] = {}
        # Pre-register the known series so an empty snapshot still lists them.
        for name in _COUNTERS:
            self._counters[(name, ())] = 0
        for name in _HISTOGRAMS:
            self._histograms[(name, ())] = _Histogram()

    # ---- recording ----------------------------------------------------
    def inc(
        self,
        name: str,
        *,
        labels: Optional[Dict[str, str]] = None,
        by: int = 1,
    ) -> None:
        if by == 0:
            return
        key = (name, _labels(labels))
        try:
            with self._lock:
                self._counters[key] = self._counters.get(key, 0) + by
        except Exception:  # pragma: no cover - metrics must never break dispatch
            pass

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        try:
            key = (name, _labels(labels))
            with self._lock:
                hist = self._histograms.get(key)
                if hist is None:
                    hist = _Histogram()
                    self._histograms[key] = hist
                hist.observe(float(value))
        except Exception:  # pragma: no cover
            pass

    # ---- introspection ------------------------------------------------
    def snapshot(self) -> Dict[str, object]:
        """Plain-dict view. JSON-serialisable."""
        with self._lock:
            counters: Dict[str, List[Dict[str, object]]] = {}
            for (name, labels), value in self._counters.items():
                counters.setdefault(name, []).append(
                    {"labels": dict(labels), "value": value}
                )
            histograms: Dict[str, List[Dict[str, object]]] = {}
            for (name, labels), hist in self._histograms.items():
                histograms.setdefault(name, []).append(
                    {"labels": dict(labels), **hist.as_dict()}
                )
        return {"counters": counters, "histograms": histograms}

    def render_prometheus(self) -> str:
        """Prometheus text exposition (OpenMetrics-compatible subset).

        cp1252-safe: the only non-ASCII risk would be in label values, which
        we escape according to spec.
        """
        lines: List[str] = []
        with self._lock:
            counters = list(self._counters.items())
            histograms = list(self._histograms.items())

        # Counters.
        by_name: Dict[str, List[Tuple[LabelSet, int]]] = {}
        for (name, labels), value in counters:
            by_name.setdefault(name, []).append((labels, value))
        for name in sorted(by_name):
            help_line = _HELP.get(name, "agent2society metric")
            lines.append(f"# HELP {name} {help_line}")
            lines.append(f"# TYPE {name} counter")
            for labels, value in sorted(by_name[name]):
                lines.append(f"{name}{_render_labels(labels)} {value}")

        # Histograms (rendered as summary-style: _sum, _count, _min, _max).
        by_name_h: Dict[str, List[Tuple[LabelSet, _Histogram]]] = {}
        for (name, labels), hist in histograms:
            by_name_h.setdefault(name, []).append((labels, hist))
        for name in sorted(by_name_h):
            help_line = _HELP.get(name, "agent2society metric")
            lines.append(f"# HELP {name} {help_line}")
            lines.append(f"# TYPE {name} summary")
            for labels, hist in sorted(by_name_h[name], key=lambda kv: kv[0]):
                lbl = _render_labels(labels)
                lines.append(f"{name}_count{lbl} {hist.count}")
                lines.append(f"{name}_sum{lbl} {hist.total:.6f}")
                if hist.count > 0:
                    lines.append(f"{name}_min{lbl} {hist.minimum:.6f}")
                    lines.append(f"{name}_max{lbl} {hist.maximum:.6f}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Wipe all data. Mostly useful in tests."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            for name in _COUNTERS:
                self._counters[(name, ())] = 0
            for name in _HISTOGRAMS:
                self._histograms[(name, ())] = _Histogram()


def _render_labels(labels: LabelSet) -> str:
    if not labels:
        return ""
    parts = []
    for k, v in labels:
        # Prometheus escape: backslash, double quote, newline.
        v_esc = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        parts.append(f'{k}="{v_esc}"')
    return "{" + ",".join(parts) + "}"
