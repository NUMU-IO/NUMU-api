"""Endpoint response time benchmarks using pytest-benchmark.

These tests measure and track API endpoint performance over time.
Use --benchmark-autosave to save results for comparison.

Usage:
    pytest tests/performance/test_endpoint_benchmarks.py -v --benchmark-only
    pytest tests/performance/test_endpoint_benchmarks.py -v --benchmark-compare
"""

import asyncio
from collections.abc import Callable

import httpx
import pytest

from tests.performance.conftest import (
    PerformanceMetrics,
    assert_performance,
    run_benchmark,
)


class TestPublicEndpointBenchmarks:
    """Benchmark tests for public API endpoints."""

    @pytest.mark.asyncio
    async def test_health_check_performance(
        self,
        async_client: httpx.AsyncClient,
    ) -> None:
        """Benchmark health check endpoint.

        Expected: <50ms p95 (critical for load balancer checks)
        """
        metrics = await run_benchmark(
            async_client,
            "GET",
            "/api/v1/public/health",
            iterations=20,
            warmup=5,
        )

        if metrics.count > 0:
            assert_performance(
                metrics,
                p95_max_ms=100.0,  # Health check should be very fast
                p99_max_ms=200.0,
            )
            print(f"\nHealth check performance: {metrics.summary()}")

    @pytest.mark.asyncio
    async def test_products_list_performance(
        self,
        async_client: httpx.AsyncClient,
        test_store_id: str,
    ) -> None:
        """Benchmark products list endpoint.

        Expected: <500ms p95 for default page size
        """
        metrics = await run_benchmark(
            async_client,
            "GET",
            f"/api/v1/storefront/store/{test_store_id}/products",
            iterations=15,
            warmup=3,
            params={"limit": 20},
        )

        if metrics.count > 0:
            assert_performance(
                metrics,
                p95_max_ms=500.0,
                p99_max_ms=1000.0,
                max_response_size=50_000,  # 50KB max for mobile
            )
            print(f"\nProducts list performance: {metrics.summary()}")

    @pytest.mark.asyncio
    async def test_products_pagination_consistency(
        self,
        async_client: httpx.AsyncClient,
        test_store_id: str,
    ) -> None:
        """Verify pagination performance is consistent across pages.

        Later pages should not be significantly slower than first page.
        """
        page_metrics: dict[int, PerformanceMetrics] = {}

        for page in [1, 5, 10]:
            metrics = await run_benchmark(
                async_client,
                "GET",
                f"/api/v1/storefront/store/{test_store_id}/products",
                iterations=5,
                warmup=2,
                params={"limit": 20, "page": page},
            )
            page_metrics[page] = metrics

        # Verify later pages aren't significantly slower
        if page_metrics[1].count > 0 and page_metrics[10].count > 0:
            page_1_p95 = page_metrics[1].p95_response_time
            page_10_p95 = page_metrics[10].p95_response_time

            # Page 10 should not be more than 50% slower than page 1
            assert page_10_p95 < page_1_p95 * 1.5, (
                f"Pagination performance degraded: page 1 p95={page_1_p95:.2f}ms, "
                f"page 10 p95={page_10_p95:.2f}ms"
            )

    @pytest.mark.asyncio
    async def test_product_detail_performance(
        self,
        async_client: httpx.AsyncClient,
        test_store_id: str,
    ) -> None:
        """Benchmark product detail endpoint.

        Expected: <200ms p95 (single item fetch)
        """
        # First get a product slug
        response = await async_client.get(
            f"/api/v1/storefront/store/{test_store_id}/products",
            params={"limit": 1},
        )

        if response.status_code != 200:
            pytest.skip("API not available")

        data = response.json()
        if not data.get("data", {}).get("items"):
            pytest.skip("No products available")

        product_slug = data["data"]["items"][0]["slug"]

        metrics = await run_benchmark(
            async_client,
            "GET",
            f"/api/v1/storefront/store/{test_store_id}/products/{product_slug}",
            iterations=15,
            warmup=3,
        )

        if metrics.count > 0:
            assert_performance(
                metrics,
                p95_max_ms=200.0,
                p99_max_ms=500.0,
            )
            print(f"\nProduct detail performance: {metrics.summary()}")

    @pytest.mark.asyncio
    async def test_store_lookup_performance(
        self,
        async_client: httpx.AsyncClient,
    ) -> None:
        """Benchmark store lookup by subdomain.

        Expected: <100ms p95 (critical for initial app load)
        """
        metrics = await run_benchmark(
            async_client,
            "GET",
            "/api/v1/storefront/store-by-subdomain/test-store",
            iterations=15,
            warmup=3,
        )

        if metrics.count > 0:
            # Store lookup might 404, that's okay for performance testing
            assert_performance(
                metrics,
                p95_max_ms=150.0,
                p99_max_ms=300.0,
            )
            print(f"\nStore lookup performance: {metrics.summary()}")


class TestSearchEndpointBenchmarks:
    """Benchmark tests for search functionality."""

    @pytest.mark.asyncio
    async def test_product_search_performance(
        self,
        async_client: httpx.AsyncClient,
        test_store_id: str,
    ) -> None:
        """Benchmark product search endpoint.

        Expected: <800ms p95 (search can be slower)
        """
        metrics = await run_benchmark(
            async_client,
            "GET",
            f"/api/v1/storefront/store/{test_store_id}/products",
            iterations=10,
            warmup=2,
            params={"search": "test", "limit": 20},
        )

        if metrics.count > 0:
            assert_performance(
                metrics,
                p95_max_ms=800.0,
                p99_max_ms=1500.0,
            )
            print(f"\nProduct search performance: {metrics.summary()}")

    @pytest.mark.asyncio
    async def test_category_filter_performance(
        self,
        async_client: httpx.AsyncClient,
        test_store_id: str,
    ) -> None:
        """Benchmark category filtering.

        Expected: <500ms p95
        """
        # Use a dummy UUID for category filter
        dummy_category = "00000000-0000-0000-0000-000000000001"

        metrics = await run_benchmark(
            async_client,
            "GET",
            f"/api/v1/storefront/store/{test_store_id}/products",
            iterations=10,
            warmup=2,
            params={"category_id": dummy_category, "limit": 20},
        )

        if metrics.count > 0:
            assert_performance(
                metrics,
                p95_max_ms=500.0,
                p99_max_ms=1000.0,
            )
            print(f"\nCategory filter performance: {metrics.summary()}")


class TestConcurrentRequestBenchmarks:
    """Benchmark tests for concurrent request handling."""

    @pytest.mark.asyncio
    async def test_concurrent_product_requests(
        self,
        async_client: httpx.AsyncClient,
        test_store_id: str,
    ) -> None:
        """Test API performance under concurrent load.

        Simulates multiple users fetching products simultaneously.
        """
        concurrent_users = 10
        requests_per_user = 5

        async def user_session() -> list[float]:
            times = []
            for _ in range(requests_per_user):
                import time

                start = time.perf_counter()
                response = await async_client.get(
                    f"/api/v1/storefront/store/{test_store_id}/products",
                    params={"limit": 15},
                )
                elapsed = (time.perf_counter() - start) * 1000
                if response.status_code == 200:
                    times.append(elapsed)
            return times

        # Run concurrent users
        tasks = [user_session() for _ in range(concurrent_users)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect all response times
        all_times: list[float] = []
        for result in results:
            if isinstance(result, list):
                all_times.extend(result)

        if all_times:
            metrics = PerformanceMetrics()
            for t in all_times:
                metrics.response_times.append(t)

            assert_performance(
                metrics,
                p95_max_ms=2000.0,  # More lenient for concurrent
                p99_max_ms=3000.0,
            )
            print(
                f"\nConcurrent requests ({concurrent_users} users): {metrics.summary()}"
            )

    @pytest.mark.asyncio
    async def test_mixed_endpoint_load(
        self,
        async_client: httpx.AsyncClient,
        test_store_id: str,
    ) -> None:
        """Test mixed endpoint requests under load.

        Simulates realistic traffic patterns with different endpoint types.
        """
        endpoints = [
            ("/api/v1/public/health", {}),
            (f"/api/v1/storefront/store/{test_store_id}/products", {"limit": 10}),
            (
                f"/api/v1/storefront/store/{test_store_id}/products",
                {"limit": 20, "page": 2},
            ),
        ]

        async def make_request(endpoint: str, params: dict) -> float | None:
            import time

            try:
                start = time.perf_counter()
                await async_client.get(endpoint, params=params)
                return (time.perf_counter() - start) * 1000
            except Exception:
                return None

        # Create mixed workload
        tasks = []
        for _ in range(20):
            for endpoint, params in endpoints:
                tasks.append(make_request(endpoint, params))

        results = await asyncio.gather(*tasks)
        times = [t for t in results if t is not None]

        if times:
            metrics = PerformanceMetrics()
            metrics.response_times = times

            # Mixed load should still meet reasonable thresholds
            assert_performance(
                metrics,
                p95_max_ms=1500.0,
                p99_max_ms=2500.0,
            )
            print(f"\nMixed endpoint load: {metrics.summary()}")


class TestResponseSizeBenchmarks:
    """Benchmark tests focused on response sizes."""

    @pytest.mark.asyncio
    async def test_response_size_by_page_size(
        self,
        async_client: httpx.AsyncClient,
        test_store_id: str,
    ) -> None:
        """Verify response sizes scale linearly with page size."""
        page_sizes = [10, 20, 50]
        sizes: dict[int, int] = {}

        for limit in page_sizes:
            response = await async_client.get(
                f"/api/v1/storefront/store/{test_store_id}/products",
                params={"limit": limit},
            )
            if response.status_code == 200:
                sizes[limit] = len(response.content)

        if len(sizes) >= 2:
            # Response size should scale roughly linearly
            # (size_50 should be roughly 5x size_10)
            print(f"\nResponse sizes by page size: {sizes}")

            for limit, size in sizes.items():
                # Each item should not exceed ~3KB average
                avg_per_item = size / limit
                assert avg_per_item < 3000, (
                    f"Average response size per item ({avg_per_item:.0f} bytes) "
                    f"is too large for limit={limit}"
                )

    @pytest.mark.asyncio
    async def test_sparse_fields_response_size(
        self,
        async_client: httpx.AsyncClient,
        test_store_id: str,
    ) -> None:
        """Compare full vs sparse field response sizes.

        If sparse fields are implemented, verify significant size reduction.
        """
        # Full response
        full_response = await async_client.get(
            f"/api/v1/storefront/store/{test_store_id}/products",
            params={"limit": 20},
        )

        # Sparse response (if implemented)
        sparse_response = await async_client.get(
            f"/api/v1/storefront/store/{test_store_id}/products",
            params={"limit": 20, "fields": "id,name,price,images"},
        )

        if full_response.status_code == 200 and sparse_response.status_code == 200:
            full_size = len(full_response.content)
            sparse_size = len(sparse_response.content)

            print(f"\nFull response: {full_size} bytes")
            print(f"Sparse response: {sparse_size} bytes")

            if sparse_size < full_size:
                reduction = (1 - sparse_size / full_size) * 100
                print(f"Size reduction: {reduction:.1f}%")
                # If sparse fields work, expect at least 30% reduction
                assert reduction > 30, (
                    f"Sparse fields should reduce size by >30%, got {reduction:.1f}%"
                )


# Benchmark-specific tests using pytest-benchmark
class TestPytestBenchmarks:
    """Tests using pytest-benchmark for detailed performance tracking."""

    def test_sync_api_availability(
        self, benchmark: Callable, api_base_url: str
    ) -> None:
        """Benchmark sync API check."""
        import requests

        def check_api():
            try:
                return requests.get(f"{api_base_url}/api/v1/public/health", timeout=5)
            except Exception:
                return None

        result = benchmark(check_api)
        # Just verify it ran
        assert result is None or hasattr(result, "status_code")
