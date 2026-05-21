"""Prometheus-exposed metrics for the NUMU API (Step 16).

Sibling to :mod:`src.infrastructure.observability.metrics`, which is the
log-backed shim used by existing call sites. That shim stays — too many
call sites already import it — and this module adds a real Prometheus
exposition pipeline for the storefront-perf dashboards (plan §2).

Hard rule on labels: **every label value must be bounded**. Prometheus
storage cost is dominated by the active series count, and a single
unbounded label (raw URL, store UUID, customer UUID, …) can explode
that count overnight. The metrics here use:

* ``route`` — the route's path *template* (``/storefront/store/{store_id}/products``),
  not the interpolated path. Pulled from ``request.scope["route"].path``.
* ``method`` — fixed-cardinality HTTP verb set.
* ``status`` — ``2xx`` / ``3xx`` / ``4xx`` / ``5xx`` buckets, not the
  raw integer status code (which is bounded but doesn't add useful
  cardinality at dashboard level).
* ``layer`` — fixed: ``storefront`` / ``product`` / ``promotion``.
* ``reason`` — fixed enum of invalidation triggers.

Adding a new label? Audit it against the cardinality rule. If you can't
prove the value set is < ~100 distinct values across the fleet,
the metric belongs in structured logs, not Prometheus.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Dedicated registry so test code can spin up isolated registries
# without touching the global one. Production code reads from
# :data:`REGISTRY`.
REGISTRY = CollectorRegistry(auto_describe=True)


# ────────────────────────────────────────────────────────────── #
# HTTP                                                            #
# ────────────────────────────────────────────────────────────── #

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("route", "method", "status"),
    buckets=(
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
    ),
    registry=REGISTRY,
)

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests processed by the API.",
    labelnames=("route", "method", "status"),
    registry=REGISTRY,
)

http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "Currently in-flight HTTP requests.",
    labelnames=("route",),
    registry=REGISTRY,
)


# ────────────────────────────────────────────────────────────── #
# Cache                                                           #
# ────────────────────────────────────────────────────────────── #

# Layer is a fixed enum — see plan §2.3.
CACHE_LAYERS = ("storefront", "product", "promotion")

cache_hit_total = Counter(
    "cache_hit_total",
    "Cache lookups that returned a populated payload.",
    labelnames=("layer",),
    registry=REGISTRY,
)

cache_miss_total = Counter(
    "cache_miss_total",
    "Cache lookups that returned no payload (real miss, not negative-cache).",
    labelnames=("layer",),
    registry=REGISTRY,
)

cache_negative_hit_total = Counter(
    "cache_negative_hit_total",
    "Cache lookups that returned a stored 'missing' sentinel (e.g. 404 negative caching).",
    labelnames=("layer",),
    registry=REGISTRY,
)

cache_invalidate_total = Counter(
    "cache_invalidate_total",
    "Explicit cache invalidations issued by mutation handlers.",
    labelnames=("layer", "reason"),
    registry=REGISTRY,
)


# ────────────────────────────────────────────────────────────── #
# Database connection pool                                        #
# ────────────────────────────────────────────────────────────── #

# Pool gauges are set via callbacks from the SQLAlchemy engine's
# pool listener (see src/infrastructure/database/connection.py). Using
# Gauge.set_function isn't safe with multiprocess-mode workers; the
# Step-16 deploy is single-process per container, so plain ``set``
# from the listener works.
db_connections_in_use = Gauge(
    "db_connections_in_use",
    "Connections currently checked out of the SQLAlchemy pool.",
    registry=REGISTRY,
)

db_connections_pool_size = Gauge(
    "db_connections_pool_size",
    "Configured pool size (excluding overflow).",
    registry=REGISTRY,
)

db_connections_overflow = Gauge(
    "db_connections_overflow",
    "Connections currently sourced from the overflow pool.",
    registry=REGISTRY,
)

db_pool_wait_seconds = Histogram(
    "db_pool_wait_seconds",
    "Time spent waiting for a connection from the SQLAlchemy pool.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0),
    registry=REGISTRY,
)


# ────────────────────────────────────────────────────────────── #
# Helpers                                                         #
# ────────────────────────────────────────────────────────────── #


def status_bucket(status_code: int) -> str:
    """Coarsen an HTTP status code into a fixed-cardinality bucket label.

    Keeps the ``status`` label at four values (``2xx`` / ``3xx`` /
    ``4xx`` / ``5xx``) so dashboards stay readable and the active-series
    count stays bounded.
    """
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    if 500 <= status_code < 600:
        return "5xx"
    return "other"


def render_exposition() -> tuple[bytes, str]:
    """Render the current registry as Prometheus text-format bytes
    plus the Content-Type header value.

    The /metrics endpoint hands the result straight to the response.
    """
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def record_cache_hit(layer: str) -> None:
    """Idempotency-friendly helper — keeps cache modules from having to
    import the metric directly (lighter touch on the call sites)."""
    cache_hit_total.labels(layer=layer).inc()


def record_cache_miss(layer: str) -> None:
    cache_miss_total.labels(layer=layer).inc()


def record_cache_negative_hit(layer: str) -> None:
    cache_negative_hit_total.labels(layer=layer).inc()


def record_cache_invalidate(layer: str, reason: str = "explicit") -> None:
    cache_invalidate_total.labels(layer=layer, reason=reason).inc()
