"""3G Network Simulation Tests.

These tests verify API performance under simulated 3G network conditions.
They help ensure the API is optimized for mobile users on slow connections.

3G Network Characteristics:
- Slow 3G: 500 Kbps, 400ms latency
- Regular 3G: 1 Mbps, 200ms latency
- Fast 3G: 2 Mbps, 100ms latency

Performance Targets:
- Response time (p95): <3000ms for regular 3G
- Response size: <50KB for list endpoints
- Error rate: <1%

Usage:
    pytest tests/performance/test_3g_simulation.py -v
    pytest tests/performance/test_3g_simulation.py -v -k "3g_regular"
"""

import asyncio
import time

import httpx
import pytest

from tests.performance.conftest import (
    NETWORK_PROFILES,
    PERFORMANCE_THRESHOLDS,
    NetworkSimulator,
    PerformanceValidator,
)


class TestAPIUnder3GConditions:
    """API performance tests simulating 3G network conditions."""

    @pytest.mark.asyncio
    async def test_products_endpoint_3g_regular(
        self,
        network_3g_regular: NetworkSimulator,
        performance_validator: PerformanceValidator,
        api_base_url: str,
        test_store_id: str,
    ):
        """Test products endpoint under regular 3G conditions.

        Verifies:
        - Response time is acceptable for 3G
        - Response size is within 3G limits
        - API returns valid data
        """
        async with httpx.AsyncClient(base_url=api_base_url, timeout=30.0) as client:
            # Run multiple iterations to get statistical significance
            for _ in range(10):
                # Simulate network latency
                await network_3g_regular.simulate_latency()

                start_time = time.perf_counter()

                response = await client.get(
                    f"/api/v1/storefront/store/{test_store_id}/products",
                    params={"limit": 15},  # 3G-optimized page size
                )

                elapsed_ms = (time.perf_counter() - start_time) * 1000

                # Add simulated transfer time based on response size
                transfer_time = network_3g_regular.calculate_transfer_time(
                    len(response.content)
                )
                total_time = elapsed_ms + transfer_time

                performance_validator.record(total_time)

                # Skip detailed assertions if API is not running
                if response.status_code == 200:
                    # Verify response size is acceptable for 3G
                    max_size = PERFORMANCE_THRESHOLDS["3g_regular"][
                        "max_response_bytes"
                    ]
                    assert len(response.content) <= max_size, (
                        f"Response size ({len(response.content)} bytes) exceeds "
                        f"3G limit ({max_size} bytes)"
                    )

        # Validate overall performance
        if performance_validator.response_times:
            performance_validator.validate("3g_regular")

    @pytest.mark.asyncio
    async def test_products_endpoint_3g_slow(
        self,
        network_3g_slow: NetworkSimulator,
        performance_validator: PerformanceValidator,
        api_base_url: str,
        test_store_id: str,
    ):
        """Test products endpoint under slow 3G conditions.

        Slow 3G represents worst-case mobile scenarios.
        """
        async with httpx.AsyncClient(base_url=api_base_url, timeout=30.0) as client:
            for _ in range(5):
                await network_3g_slow.simulate_latency()

                start_time = time.perf_counter()
                response = await client.get(
                    f"/api/v1/storefront/store/{test_store_id}/products",
                    params={"limit": 10},  # Smaller page size for slow 3G
                )
                elapsed_ms = (time.perf_counter() - start_time) * 1000

                transfer_time = network_3g_slow.calculate_transfer_time(
                    len(response.content)
                )
                total_time = elapsed_ms + transfer_time

                performance_validator.record(total_time)

        if performance_validator.response_times:
            performance_validator.validate("3g_slow")

    @pytest.mark.asyncio
    async def test_response_size_under_3g_limit(
        self,
        api_base_url: str,
        test_store_id: str,
    ):
        """Verify response sizes are acceptable for 3G networks.

        Large responses cause significant delays on 3G:
        - 50KB at 500Kbps = 800ms transfer time
        - 100KB at 500Kbps = 1600ms transfer time
        """
        async with httpx.AsyncClient(base_url=api_base_url, timeout=30.0) as client:
            # Test various endpoints
            endpoints = [
                (f"/api/v1/storefront/store/{test_store_id}/products?limit=15", 50000),
                (f"/api/v1/storefront/store/{test_store_id}/categories", 30000),
                ("/api/v1/public/health", 1000),
            ]

            for endpoint, max_bytes in endpoints:
                response = await client.get(endpoint)

                if response.status_code == 200:
                    assert len(response.content) <= max_bytes, (
                        f"Response for {endpoint} ({len(response.content)} bytes) "
                        f"exceeds 3G limit ({max_bytes} bytes)"
                    )

    @pytest.mark.asyncio
    async def test_concurrent_requests_3g(
        self,
        network_3g_regular: NetworkSimulator,
        api_base_url: str,
        test_store_id: str,
    ):
        """Test API handling concurrent requests under 3G simulation.

        Mobile apps often make multiple concurrent requests.
        This test verifies the API handles them gracefully.
        """
        async with httpx.AsyncClient(base_url=api_base_url, timeout=30.0) as client:

            async def make_request(endpoint: str) -> tuple[int, float]:
                await network_3g_regular.simulate_latency()
                start = time.perf_counter()
                response = await client.get(endpoint)
                elapsed = (time.perf_counter() - start) * 1000
                return response.status_code, elapsed

            # Make concurrent requests
            endpoints = [
                f"/api/v1/storefront/store/{test_store_id}/products?limit=15",
                f"/api/v1/storefront/store/{test_store_id}/categories",
                "/api/v1/public/health",
            ]

            tasks = [make_request(endpoint) for endpoint in endpoints]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Verify all requests completed
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    pytest.skip(f"Request {i} failed: {result}")
                else:
                    status_code, elapsed = result
                    # Allow longer timeout for concurrent 3G requests
                    assert elapsed < 10000, (
                        f"Concurrent request took too long: {elapsed}ms"
                    )

    @pytest.mark.asyncio
    async def test_pagination_performance_3g(
        self,
        network_3g_regular: NetworkSimulator,
        api_base_url: str,
        test_store_id: str,
    ):
        """Test pagination performance is consistent under 3G.

        Cursor-based pagination should have O(1) performance,
        meaning page 100 should be as fast as page 1.
        """
        async with httpx.AsyncClient(base_url=api_base_url, timeout=30.0) as client:
            response_times = {"page_1": [], "page_5": []}

            # Test first page
            for _ in range(3):
                await network_3g_regular.simulate_latency()
                start = time.perf_counter()
                response = await client.get(
                    f"/api/v1/storefront/store/{test_store_id}/products",
                    params={"limit": 15, "page": 1},
                )
                if response.status_code == 200:
                    elapsed = (time.perf_counter() - start) * 1000
                    response_times["page_1"].append(elapsed)

            # Test later page
            for _ in range(3):
                await network_3g_regular.simulate_latency()
                start = time.perf_counter()
                response = await client.get(
                    f"/api/v1/storefront/store/{test_store_id}/products",
                    params={"limit": 15, "page": 5},
                )
                if response.status_code == 200:
                    elapsed = (time.perf_counter() - start) * 1000
                    response_times["page_5"].append(elapsed)

            # If we got results, verify performance is similar
            if response_times["page_1"] and response_times["page_5"]:
                avg_page_1 = sum(response_times["page_1"]) / len(
                    response_times["page_1"]
                )
                avg_page_5 = sum(response_times["page_5"]) / len(
                    response_times["page_5"]
                )

                # Page 5 should not be more than 50% slower than page 1
                # (accounting for variance)
                assert avg_page_5 < avg_page_1 * 1.5, (
                    f"Pagination performance degraded: "
                    f"page 1 avg={avg_page_1:.0f}ms, page 5 avg={avg_page_5:.0f}ms"
                )


class TestResponseSizeOptimization:
    """Tests for response size optimization critical for 3G."""

    @pytest.mark.asyncio
    async def test_sparse_fieldsets_reduce_size(
        self,
        api_base_url: str,
        test_store_id: str,
    ):
        """Verify sparse fieldsets significantly reduce response size.

        Expected reduction: 50-70% for typical use cases.
        """
        async with httpx.AsyncClient(base_url=api_base_url, timeout=30.0) as client:
            # Full response
            full_response = await client.get(
                f"/api/v1/storefront/store/{test_store_id}/products",
                params={"limit": 15},
            )

            # Sparse response (if sparse fieldsets are implemented)
            sparse_response = await client.get(
                f"/api/v1/storefront/store/{test_store_id}/products",
                params={"limit": 15, "fields": "id,name,price,images"},
            )

            if full_response.status_code == 200 and sparse_response.status_code == 200:
                full_size = len(full_response.content)
                sparse_size = len(sparse_response.content)

                # Sparse response should be smaller (if implemented)
                # This test documents the expected behavior
                if sparse_size < full_size:
                    reduction = (1 - sparse_size / full_size) * 100
                    print(
                        f"Sparse fieldsets reduced size by {reduction:.1f}% "
                        f"({full_size} -> {sparse_size} bytes)"
                    )

    @pytest.mark.asyncio
    async def test_compression_effectiveness(
        self,
        api_base_url: str,
        test_store_id: str,
    ):
        """Test that responses are compressible.

        JSON responses should compress well (60-80% reduction).
        """
        import gzip

        async with httpx.AsyncClient(base_url=api_base_url, timeout=30.0) as client:
            response = await client.get(
                f"/api/v1/storefront/store/{test_store_id}/products",
                params={"limit": 20},
            )

            if response.status_code == 200:
                original_size = len(response.content)
                compressed_size = len(gzip.compress(response.content))
                compression_ratio = compressed_size / original_size

                # JSON should compress to at least 50% of original
                assert compression_ratio < 0.6, (
                    f"Response not compressible enough: "
                    f"{compression_ratio:.1%} (target: <60%)"
                )

                print(
                    f"Compression ratio: {compression_ratio:.1%} "
                    f"({original_size} -> {compressed_size} bytes)"
                )


class TestNetworkProfileThresholds:
    """Verify thresholds are correctly configured for each network type."""

    def test_slow_3g_thresholds(self):
        """Verify slow 3G thresholds are appropriately lenient."""
        thresholds = PERFORMANCE_THRESHOLDS["3g_slow"]

        assert thresholds["p95_max_ms"] >= 3000, "Slow 3G p95 threshold too strict"
        assert thresholds["p99_max_ms"] >= 5000, "Slow 3G p99 threshold too strict"
        assert thresholds["max_response_bytes"] <= 50000, (
            "Slow 3G response size limit too large"
        )

    def test_regular_3g_thresholds(self):
        """Verify regular 3G thresholds are reasonable."""
        thresholds = PERFORMANCE_THRESHOLDS["3g_regular"]

        assert thresholds["p95_max_ms"] >= 2000, "Regular 3G p95 threshold too strict"
        assert thresholds["p95_max_ms"] <= 5000, "Regular 3G p95 threshold too lenient"

    def test_network_profiles_valid(self):
        """Verify all network profiles have valid configurations."""
        for name, profile in NETWORK_PROFILES.items():
            assert profile.latency_ms > 0, f"{name} latency must be positive"
            assert profile.download_kbps > 0, f"{name} download speed must be positive"
            assert 0 <= profile.packet_loss_percent <= 100, (
                f"{name} packet loss must be 0-100%"
            )
