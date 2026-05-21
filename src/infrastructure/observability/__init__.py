"""Observability primitives (metrics, tracing) for business events.

Exports a small counter / timer surface that today emits structlog
events in the shape a log-aggregator (Datadog, Loki, CloudWatch) can
derive time-series metrics from. When the infra team is ready to run
``prometheus_client`` properly, swap the implementation in ``metrics``
— the call sites stay unchanged.
"""

from src.infrastructure.observability.metrics import (
    Counter,
    Timer,
    counter,
    timer,
)
from src.infrastructure.observability.prometheus_metrics import (
    REGISTRY,
    record_cache_hit,
    record_cache_invalidate,
    record_cache_miss,
    record_cache_negative_hit,
    render_exposition,
    status_bucket,
)

__all__ = [
    "Counter",
    "REGISTRY",
    "Timer",
    "counter",
    "record_cache_hit",
    "record_cache_invalidate",
    "record_cache_miss",
    "record_cache_negative_hit",
    "render_exposition",
    "status_bucket",
    "timer",
]
