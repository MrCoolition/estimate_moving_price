from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

_REGISTRY: List["_MetricBase"] = []


class _MetricBase:
    def __init__(self, name: str, documentation: str, labelnames: Iterable[str] | None = None):
        self.name = name
        self.documentation = documentation
        self.labelnames: Tuple[str, ...] = tuple(labelnames or ())
        _REGISTRY.append(self)

    def _validate_labels(self, labels: Dict[str, str]) -> Tuple[str, ...]:
        if set(labels.keys()) != set(self.labelnames):
            missing = set(self.labelnames) - set(labels.keys())
            extra = set(labels.keys()) - set(self.labelnames)
            problems = []
            if missing:
                problems.append(f"missing: {sorted(missing)}")
            if extra:
                problems.append(f"extra: {sorted(extra)}")
            raise ValueError(f"Label mismatch for {self.name}: {'; '.join(problems)}")
        return tuple(labels[label] for label in self.labelnames)

    def samples(self):
        raise NotImplementedError

    @property
    def type(self) -> str:
        raise NotImplementedError


class Counter(_MetricBase):
    def __init__(self, name: str, documentation: str, labelnames: Iterable[str] | None = None):
        super().__init__(name, documentation, labelnames)
        self._values: Dict[Tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0) -> None:
        self._increment((), amount)

    def _increment(self, key: Tuple[str, ...], amount: float) -> None:
        self._values[key] = self._values.get(key, 0.0) + amount

    def labels(self, **labels: str) -> "Counter":
        key = self._validate_labels(labels)
        child = Counter(self.name, self.documentation, self.labelnames)
        child._values = self._values
        child._increment = lambda _, amount: self._increment(key, amount)
        child.labels = lambda **_: child
        return child

    def samples(self):
        for key, value in self._values.items():
            labels = dict(zip(self.labelnames, key)) if self.labelnames else {}
            yield self.name, labels, value

    @property
    def type(self) -> str:
        return "counter"


class Histogram(_MetricBase):
    def __init__(self, name: str, documentation: str, buckets: Iterable[float], labelnames: Iterable[str] | None = None):
        super().__init__(name, documentation, labelnames)
        self._buckets = tuple(sorted(buckets))
        if not self._buckets or self._buckets[-1] != float("inf"):
            self._buckets += (float("inf"),)
        self._counts: Dict[Tuple[str, ...], List[int]] = {}
        self._sums: Dict[Tuple[str, ...], float] = {}

    def observe(self, value: float) -> None:
        self._observe((), value)

    def _observe(self, key: Tuple[str, ...], value: float) -> None:
        counts = self._counts.setdefault(key, [0 for _ in self._buckets])
        for idx, upper in enumerate(self._buckets):
            if value <= upper:
                counts[idx] += 1
                break
        self._sums[key] = self._sums.get(key, 0.0) + value

    def labels(self, **labels: str) -> "Histogram":
        key = self._validate_labels(labels)
        child = Histogram(self.name, self.documentation, self._buckets, self.labelnames)
        child._counts = self._counts
        child._sums = self._sums
        child._observe = lambda _, value: self._observe(key, value)
        child.labels = lambda **_: child
        return child

    def samples(self):
        for key, counts in self._counts.items():
            labels = dict(zip(self.labelnames, key)) if self.labelnames else {}
            cumulative = 0
            for idx, upper in enumerate(self._buckets):
                cumulative += counts[idx]
                bucket_labels = dict(labels)
                bucket_labels["le"] = "+Inf" if upper == float("inf") else str(upper)
                yield f"{self.name}_bucket", bucket_labels, cumulative
            yield f"{self.name}_count", labels, cumulative
            yield f"{self.name}_sum", labels, self._sums.get(key, 0.0)

    @property
    def type(self) -> str:
        return "histogram"


def generate_latest() -> bytes:
    lines: List[str] = []
    for metric in _REGISTRY:
        lines.append(f"# HELP {metric.name} {metric.documentation}")
        lines.append(f"# TYPE {metric.name} {metric.type}")
        for sample_name, labels, value in metric.samples():
            if labels:
                label_str = ",".join(f"{k}=\"{v}\"" for k, v in sorted(labels.items()))
                lines.append(f"{sample_name}{{{label_str}}} {value}")
            else:
                lines.append(f"{sample_name} {value}")
    return "\n".join(lines).encode("utf-8")
