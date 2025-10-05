from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterable

from .metrics import Counter, Histogram

logger = logging.getLogger("estimate_moving_price")
logger.setLevel(logging.INFO)

QUOTE_LATENCY_MS = Histogram(
    "quote_latency_ms",
    "Quote latency in milliseconds",
    buckets=(50, 100, 200, 300, 400, 500, 750, 1000, 1500, 2000),
)
QUOTE_SUCCESS = Counter("quote_success_total", "Successful quotes")
QUOTE_ERROR = Counter("quote_error_total", "Errored quotes")
OPTIMIZER_CANDIDATES = Histogram(
    "optimizer_candidates_evaluated",
    "Number of optimizer candidates evaluated",
    buckets=(1, 2, 3, 4, 5, 6, 10, 15, 20),
)
OPTIMIZER_WINNERS_MOVERS = Counter("optimizer_winner_movers", "Winning mover counts", labelnames=("movers",))
OPTIMIZER_WINNERS_TRUCKS = Counter("optimizer_winner_trucks", "Winning truck counts", labelnames=("trucks",))
UNKNOWN_ITEM_RATE = Counter("unknown_item_total", "Unknown items received")
ALIAS_HIT_RATE = Counter("alias_hit_total", "Alias matches", labelnames=("approximate",))

class _Span:
    def __init__(self, name: str):
        self.name = name

    def __enter__(self) -> "_Span":  # pragma: no cover - simple helper
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - simple helper
        return None

    def set_attribute(self, key: str, value: Any) -> None:
        logger.debug("span_attribute", extra={"span": self.name, key: value})


class _Tracer:
    def start_as_current_span(self, name: str) -> _Span:
        return _Span(name)


tracer = _Tracer()


def record_quote_success(latency_ms: float, candidates: int, movers: int, trucks: int) -> None:
    QUOTE_LATENCY_MS.observe(latency_ms)
    QUOTE_SUCCESS.inc()
    OPTIMIZER_CANDIDATES.observe(candidates)
    OPTIMIZER_WINNERS_MOVERS.labels(movers=str(movers)).inc()
    OPTIMIZER_WINNERS_TRUCKS.labels(trucks=str(trucks)).inc()


def record_quote_error() -> None:
    QUOTE_ERROR.inc()


def record_alias_hit(approximate: bool) -> None:
    ALIAS_HIT_RATE.labels(approximate=str(approximate)).inc()


def record_unknown_item() -> None:
    UNKNOWN_ITEM_RATE.inc()


@contextmanager
def span(name: str, **attributes: Any):
    with tracer.start_as_current_span(name) as current_span:
        if attributes:
            for key, value in attributes.items():
                current_span.set_attribute(key, value)
        yield current_span


def hash_items(items: Iterable[str]) -> str:
    joined = "|".join(sorted(items))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def structured_log(event: str, **payload: Any) -> None:
    entry: Dict[str, Any] = {"event": event, **payload}
    logger.info(json.dumps(entry))
