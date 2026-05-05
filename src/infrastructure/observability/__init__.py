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

__all__ = ["Counter", "Timer", "counter", "timer"]
