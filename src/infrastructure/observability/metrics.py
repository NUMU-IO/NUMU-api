"""Structured-log-backed metrics.

Emits a dedicated ``metric=<name>`` event so log aggregators can pivot
into time-series dashboards without any additional infra. The surface
mimics ``prometheus_client`` so swapping to a real Prometheus exporter
later is a one-file change with zero call-site churn.

Design rules:

  * Call sites never import structlog directly — they import
    :func:`counter` / :func:`timer`, so the backend is one place.
  * Labels are plain kwargs; we coerce to strings inside the module so
    callers don't have to think about formatting.
  * ``timer()`` returns an awaitable context manager so async code
    paths don't need an extra ``start = time.monotonic()`` dance.

Naming convention: ``<product>_<subject>_<unit>`` — matches Prometheus
conventions and keeps the log events self-describing.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from src.config.logging_config import get_logger

_logger = get_logger("metrics")


class Counter:
    """Monotonically-increasing counter.

    Usage:
        c = Counter("instapay_proof_submissions_total",
                    description="...",
                    labels=["status", "store_id"])
        c.inc(status="auto_approved", store_id=str(store.id))
    """

    def __init__(
        self,
        name: str,
        *,
        description: str = "",
        labels: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self._label_names = labels or []

    def inc(self, amount: int = 1, **labels: Any) -> None:
        event = {
            "metric": self.name,
            "metric_type": "counter",
            "delta": amount,
        }
        for key in self._label_names:
            event[f"label_{key}"] = str(labels.get(key, ""))
        # Attach any extra labels the caller provided even if they
        # weren't pre-declared — log aggregators can still pivot.
        for key, value in labels.items():
            if key not in self._label_names:
                event[f"label_{key}"] = str(value)
        _logger.info("metric_counter", **event)


class Timer:
    """Records an elapsed-time observation in seconds.

    Used as an async context manager:

        async with Timer("instapay_review_latency_seconds").measure(
            status="approved", store_id=str(store.id),
        ):
            ...
    """

    def __init__(
        self,
        name: str,
        *,
        description: str = "",
        labels: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self._label_names = labels or []

    def observe(self, seconds: float, **labels: Any) -> None:
        event = {
            "metric": self.name,
            "metric_type": "timer",
            "value_seconds": round(seconds, 4),
        }
        for key in self._label_names:
            event[f"label_{key}"] = str(labels.get(key, ""))
        for key, value in labels.items():
            if key not in self._label_names:
                event[f"label_{key}"] = str(value)
        _logger.info("metric_timer", **event)

    @asynccontextmanager
    async def measure(self, **labels: Any):
        start = time.monotonic()
        try:
            yield
        finally:
            self.observe(time.monotonic() - start, **labels)


# Convenience factories so call sites don't have to import the classes.
def counter(
    name: str, *, description: str = "", labels: list[str] | None = None
) -> Counter:
    return Counter(name, description=description, labels=labels)


def timer(
    name: str, *, description: str = "", labels: list[str] | None = None
) -> Timer:
    return Timer(name, description=description, labels=labels)
