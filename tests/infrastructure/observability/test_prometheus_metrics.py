"""Unit tests for the Step 16 Prometheus metrics pipeline.

Two threads:

1. The metric module itself — counters/histograms/gauges expose the
   expected names, render through ``generate_latest`` without error,
   and have bounded label cardinality (the helpers reject obvious
   high-cardinality usage).

2. The wiring that emits them — exercise the cache hit/miss helpers,
   the timing middleware against a minimal Starlette app, and the
   timing middleware's route-template label resolution. We never
   boot the full FastAPI app (Step 04 lesson: project conftest +
   async pool fixtures break on Windows) — minimal Starlette router
   gives us a clean test surface.

Note on registry isolation: prometheus_client metrics live on a
module-level registry, so a counter value here persists across test
runs. The tests assert *deltas* (sample after - before), not absolute
values, so order-of-execution doesn't matter.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from src.api.middleware.timing import ResponseTimeMiddleware
from src.infrastructure.observability.prometheus_metrics import (
    REGISTRY,
    cache_hit_total,
    cache_invalidate_total,
    cache_miss_total,
    cache_negative_hit_total,
    db_connections_in_use,
    http_request_duration_seconds,
    http_requests_total,
    record_cache_hit,
    record_cache_invalidate,
    record_cache_miss,
    record_cache_negative_hit,
    status_bucket,
)


def _sample(metric: Any, **labels: str) -> float:
    """Read the current sample value off a Counter/Gauge family."""
    return metric.labels(**labels)._value.get() if labels else metric._value.get()


# ---------------------------------------------------------------- #
# status_bucket                                                     #
# ---------------------------------------------------------------- #


def test_status_bucket_buckets_codes_correctly() -> None:
    assert status_bucket(200) == "2xx"
    assert status_bucket(204) == "2xx"
    assert status_bucket(301) == "3xx"
    assert status_bucket(404) == "4xx"
    assert status_bucket(500) == "5xx"
    assert status_bucket(599) == "5xx"
    assert status_bucket(99) == "other"
    assert status_bucket(699) == "other"


# ---------------------------------------------------------------- #
# Cache helpers                                                     #
# ---------------------------------------------------------------- #


def test_record_cache_hit_increments_layer_counter() -> None:
    before = _sample(cache_hit_total, layer="storefront")
    record_cache_hit("storefront")
    record_cache_hit("storefront")
    after = _sample(cache_hit_total, layer="storefront")
    assert after - before == 2.0


def test_record_cache_miss_increments_layer_counter() -> None:
    before = _sample(cache_miss_total, layer="product")
    record_cache_miss("product")
    after = _sample(cache_miss_total, layer="product")
    assert after - before == 1.0


def test_record_cache_negative_hit_separate_from_miss() -> None:
    miss_before = _sample(cache_miss_total, layer="storefront")
    neg_before = _sample(cache_negative_hit_total, layer="storefront")
    record_cache_negative_hit("storefront")
    miss_after = _sample(cache_miss_total, layer="storefront")
    neg_after = _sample(cache_negative_hit_total, layer="storefront")
    assert neg_after - neg_before == 1.0
    assert miss_after == miss_before, "negative hit must not double-count as a miss"


def test_record_cache_invalidate_uses_reason_label() -> None:
    before = _sample(
        cache_invalidate_total, layer="storefront", reason="store_mutation"
    )
    record_cache_invalidate("storefront", reason="store_mutation")
    after = _sample(cache_invalidate_total, layer="storefront", reason="store_mutation")
    assert after - before == 1.0


def test_storefront_cache_records_hits_and_misses() -> None:
    """Smoke test: the real storefront cache emits both branches."""
    from src.infrastructure.cache.storefront_cache import StorefrontCache

    class FakeRedis:
        def __init__(self) -> None:
            self.data: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.data.get(key)

        async def setex(self, key: str, ttl: int, value: str) -> bool:
            self.data[key] = value
            return True

        async def delete(self, *keys: str) -> int:
            n = 0
            for k in keys:
                if k in self.data:
                    del self.data[k]
                    n += 1
            return n

        def pipeline(self):  # type: ignore[no-untyped-def]
            outer = self

            class _Pipe:
                def __init__(self) -> None:
                    self._ops: list[tuple[str, tuple[Any, ...]]] = []

                async def __aenter__(self) -> _Pipe:
                    return self

                async def __aexit__(self, *exc: object) -> None:
                    return None

                def setex(self, key: str, ttl: int, value: str) -> _Pipe:
                    self._ops.append(("setex", (key, ttl, value)))
                    return self

                def delete(self, *keys: str) -> _Pipe:
                    self._ops.append(("delete", keys))
                    return self

                async def execute(self) -> list[Any]:
                    out: list[Any] = []
                    for op, args in self._ops:
                        if op == "setex":
                            out.append(await outer.setex(*args))
                        elif op == "delete":
                            out.append(await outer.delete(*args))
                    return out

            return _Pipe()

    cache = StorefrontCache(redis=FakeRedis(), ttl_seconds=60)

    miss_before = _sample(cache_miss_total, layer="storefront")
    hit_before = _sample(cache_hit_total, layer="storefront")

    loop = asyncio.new_event_loop()
    try:
        # First lookup — miss
        loop.run_until_complete(cache.get_store_by_subdomain("cold"))
        # Populate then hit
        loop.run_until_complete(
            cache.set_store({"id": "abc", "subdomain": "warm", "name": "x"})
        )
        loop.run_until_complete(cache.get_store_by_subdomain("warm"))
    finally:
        loop.close()

    assert _sample(cache_miss_total, layer="storefront") - miss_before >= 1.0
    assert _sample(cache_hit_total, layer="storefront") - hit_before >= 1.0


# ---------------------------------------------------------------- #
# Timing middleware emission                                        #
# ---------------------------------------------------------------- #


def test_timing_middleware_emits_request_count_and_duration() -> None:
    """A request through ResponseTimeMiddleware must bump both
    http_requests_total and http_request_duration_seconds.

    Label note: we label by handler function name (``endpoint.__name__``),
    NOT raw path or path template — see ``_route_label`` for the
    rationale. The label for an endpoint named ``widgets_handler``
    is therefore ``widgets_handler``.
    """

    async def widgets_handler(_request: Any) -> Response:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/widgets", widgets_handler)])
    app.add_middleware(ResponseTimeMiddleware)
    client = TestClient(app)

    before = _sample(
        http_requests_total,
        route="widgets_handler",
        method="GET",
        status="2xx",
    )
    h_before = http_request_duration_seconds.labels(
        route="widgets_handler", method="GET", status="2xx"
    )._sum.get()

    response = client.get("/widgets")
    assert response.status_code == 200
    assert "x-response-time" in {k.lower() for k in response.headers.keys()}

    after = _sample(
        http_requests_total,
        route="widgets_handler",
        method="GET",
        status="2xx",
    )
    h_after = http_request_duration_seconds.labels(
        route="widgets_handler", method="GET", status="2xx"
    )._sum.get()
    assert after - before == 1.0
    assert h_after - h_before > 0.0


def test_timing_middleware_collapses_path_params_into_single_label() -> None:
    """Cardinality guard — two requests to the same handler with
    different path-param values must collapse onto one time-series.

    This is THE critical invariant for Prometheus cost. The label
    value is ``endpoint.__name__`` (constant per handler), so 10 000
    different UUID values produce ONE series, not 10 000.
    """

    async def show_store(_request: Any) -> Response:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/store/{store_id}", show_store)])
    app.add_middleware(ResponseTimeMiddleware)
    client = TestClient(app)

    before = _sample(
        http_requests_total, route="show_store", method="GET", status="2xx"
    )
    client.get("/store/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client.get("/store/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    after = _sample(http_requests_total, route="show_store", method="GET", status="2xx")

    assert after - before == 2.0, (
        "two requests to the same handler must collapse onto one series; "
        "if this fails the route label is leaking high-cardinality data "
        "and Prometheus cardinality is about to explode"
    )


def test_timing_middleware_buckets_status_codes() -> None:
    """A 503 must land on the ``5xx`` bucket, not on its own integer label."""

    async def bust(_request: Any) -> Response:
        return JSONResponse({"err": "x"}, status_code=503)

    app = Starlette(routes=[Route("/bust", bust)])
    app.add_middleware(ResponseTimeMiddleware)
    client = TestClient(app)

    before = _sample(http_requests_total, route="bust", method="GET", status="5xx")
    client.get("/bust")
    after = _sample(http_requests_total, route="bust", method="GET", status="5xx")
    assert after - before == 1.0


# ---------------------------------------------------------------- #
# Exposition smoke                                                  #
# ---------------------------------------------------------------- #


def test_generate_latest_renders_without_error() -> None:
    """``generate_latest`` must produce the Prometheus text format
    even before any metric has been touched in this process."""
    body = generate_latest(REGISTRY)
    # Always-present help/type lines for our declared metrics.
    text = body.decode("utf-8")
    assert "# HELP http_requests_total" in text
    assert "# TYPE http_requests_total counter" in text
    assert "# TYPE http_request_duration_seconds histogram" in text
    assert "# TYPE cache_hit_total counter" in text


def test_db_pool_gauge_can_be_set() -> None:
    db_connections_in_use.set(7)
    assert db_connections_in_use._value.get() == 7.0
    db_connections_in_use.set(0)
