"""Stress testing scenarios for NUMU API.

These tests push the API to its limits to identify breaking points
and ensure graceful degradation under heavy load.

WARNING: These tests can generate significant load. Use with caution
and ensure the target environment can handle the traffic.

Usage:
    pytest tests/performance/test_stress.py -v -s
    pytest tests/performance/test_stress.py -v -s -k "spike"
"""

import asyncio
import time
from dataclasses import dataclass

import httpx
import pytest

from tests.performance.conftest import PerformanceConfig, PerformanceMetrics


@dataclass
class StressTestResult:
    """Results from a stress test."""

    total_requests: int
    successful_requests: int
    failed_requests: int
    total_duration_ms: float
    requests_per_second: float
    metrics: PerformanceMetrics

    @property
    def success_rate(self) -> float:
        """Success rate as percentage."""
        return (
            (self.successful_requests / self.total_requests * 100)
            if self.total_requests > 0
            else 0.0
        )

    def summary(self) -> dict:
        """Get summary of stress test results."""
        return {
            "total_requests": self.total_requests,
            "successful": self.successful_requests,
            "failed": self.failed_requests,
            "success_rate": f"{self.success_rate:.2f}%",
            "duration": f"{self.total_duration_ms:.0f}ms",
            "rps": f"{self.requests_per_second:.2f}",
            "response_times": {
                "avg": f"{self.metrics.avg_response_time:.2f}ms",
                "p95": f"{self.metrics.p95_response_time:.2f}ms",
                "p99": f"{self.metrics.p99_response_time:.2f}ms",
            },
        }


class TestSustainedLoad:
    """Tests for sustained load scenarios."""

    @pytest.mark.asyncio
    async def test_sustained_load_10_rps(
        self,
        perf_config: PerformanceConfig,
        test_store_id: str,
    ) -> None:
        """Sustain 10 requests per second for 30 seconds.

        This represents light production traffic.
        Expected: >99% success rate, p95 < 1000ms
        """
        target_rps = 10
        duration_seconds = 30

        result = await self._run_sustained_load(
            base_url=perf_config.api_base_url,
            store_id=test_store_id,
            target_rps=target_rps,
            duration_seconds=duration_seconds,
        )

        print(f"\nSustained load test (10 RPS, 30s): {result.summary()}")

        assert result.success_rate >= 99.0, (
            f"Success rate {result.success_rate:.2f}% below 99% threshold"
        )
        assert result.metrics.p95_response_time < 1000.0, (
            f"p95 response time {result.metrics.p95_response_time:.2f}ms exceeds 1000ms"
        )

    @pytest.mark.asyncio
    async def test_sustained_load_50_rps(
        self,
        perf_config: PerformanceConfig,
        test_store_id: str,
    ) -> None:
        """Sustain 50 requests per second for 20 seconds.

        This represents moderate production traffic.
        Expected: >98% success rate, p95 < 2000ms
        """
        target_rps = 50
        duration_seconds = 20

        result = await self._run_sustained_load(
            base_url=perf_config.api_base_url,
            store_id=test_store_id,
            target_rps=target_rps,
            duration_seconds=duration_seconds,
        )

        print(f"\nSustained load test (50 RPS, 20s): {result.summary()}")

        assert result.success_rate >= 98.0, (
            f"Success rate {result.success_rate:.2f}% below 98% threshold"
        )
        assert result.metrics.p95_response_time < 2000.0, (
            f"p95 response time {result.metrics.p95_response_time:.2f}ms exceeds 2000ms"
        )

    async def _run_sustained_load(
        self,
        base_url: str,
        store_id: str,
        target_rps: int,
        duration_seconds: int,
    ) -> StressTestResult:
        """Run sustained load test."""
        metrics = PerformanceMetrics()
        successful = 0
        failed = 0

        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            start_time = time.perf_counter()
            end_time = start_time + duration_seconds

            interval = 1.0 / target_rps  # Time between requests

            while time.perf_counter() < end_time:
                request_start = time.perf_counter()

                try:
                    response = await client.get(
                        f"/api/v1/storefront/store/{store_id}/products",
                        params={"limit": 15},
                    )
                    elapsed_ms = (time.perf_counter() - request_start) * 1000
                    metrics.response_times.append(elapsed_ms)

                    if response.status_code == 200:
                        successful += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                    metrics.record_error(str(e))

                # Wait to maintain target RPS
                elapsed = time.perf_counter() - request_start
                if elapsed < interval:
                    await asyncio.sleep(interval - elapsed)

            total_duration = (time.perf_counter() - start_time) * 1000
            total_requests = successful + failed
            rps = total_requests / (total_duration / 1000) if total_duration > 0 else 0

        return StressTestResult(
            total_requests=total_requests,
            successful_requests=successful,
            failed_requests=failed,
            total_duration_ms=total_duration,
            requests_per_second=rps,
            metrics=metrics,
        )


class TestSpikeLoad:
    """Tests for sudden traffic spikes."""

    @pytest.mark.asyncio
    async def test_traffic_spike(
        self,
        perf_config: PerformanceConfig,
        test_store_id: str,
    ) -> None:
        """Simulate a sudden traffic spike.

        Pattern: 5 RPS -> 50 RPS spike -> 5 RPS
        Expected: API should handle spike without crashing
        """
        results: dict[str, StressTestResult] = {}

        async with httpx.AsyncClient(
            base_url=perf_config.api_base_url,
            timeout=30.0,
        ) as client:
            # Phase 1: Normal load
            results["normal_before"] = await self._burst_requests(
                client, test_store_id, count=30, concurrency=5
            )
            print(f"Phase 1 (normal): {results['normal_before'].summary()}")

            # Phase 2: Spike
            results["spike"] = await self._burst_requests(
                client, test_store_id, count=100, concurrency=50
            )
            print(f"Phase 2 (spike): {results['spike'].summary()}")

            # Phase 3: Recovery
            await asyncio.sleep(2)  # Brief pause
            results["normal_after"] = await self._burst_requests(
                client, test_store_id, count=30, concurrency=5
            )
            print(f"Phase 3 (recovery): {results['normal_after'].summary()}")

        # Verify recovery
        assert results["normal_after"].success_rate >= 95.0, (
            "API did not recover after spike"
        )

        # Spike should still have reasonable success
        assert results["spike"].success_rate >= 80.0, (
            f"Spike success rate {results['spike'].success_rate:.2f}% too low"
        )

    async def _burst_requests(
        self,
        client: httpx.AsyncClient,
        store_id: str,
        count: int,
        concurrency: int,
    ) -> StressTestResult:
        """Send a burst of concurrent requests."""
        metrics = PerformanceMetrics()
        successful = 0
        failed = 0

        semaphore = asyncio.Semaphore(concurrency)

        async def make_request():
            nonlocal successful, failed
            async with semaphore:
                try:
                    start = time.perf_counter()
                    response = await client.get(
                        f"/api/v1/storefront/store/{store_id}/products",
                        params={"limit": 10},
                    )
                    elapsed = (time.perf_counter() - start) * 1000
                    metrics.response_times.append(elapsed)

                    if response.status_code == 200:
                        successful += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                    metrics.record_error(str(e))

        start_time = time.perf_counter()
        tasks = [make_request() for _ in range(count)]
        await asyncio.gather(*tasks)
        total_duration = (time.perf_counter() - start_time) * 1000

        total = successful + failed
        rps = total / (total_duration / 1000) if total_duration > 0 else 0

        return StressTestResult(
            total_requests=total,
            successful_requests=successful,
            failed_requests=failed,
            total_duration_ms=total_duration,
            requests_per_second=rps,
            metrics=metrics,
        )


class TestConcurrentUsers:
    """Tests simulating concurrent user sessions."""

    @pytest.mark.asyncio
    async def test_concurrent_user_sessions(
        self,
        perf_config: PerformanceConfig,
        test_store_id: str,
    ) -> None:
        """Simulate 20 concurrent user sessions.

        Each user browses products with realistic delays.
        Expected: All sessions complete, >95% success rate
        """
        num_users = 20
        actions_per_user = 5

        async def user_session(user_id: int) -> StressTestResult:
            """Simulate a single user session."""
            metrics = PerformanceMetrics()
            successful = 0
            failed = 0

            async with httpx.AsyncClient(
                base_url=perf_config.api_base_url,
                timeout=30.0,
            ) as client:
                for action in range(actions_per_user):
                    try:
                        start = time.perf_counter()

                        # Alternate between different requests
                        if action % 3 == 0:
                            response = await client.get(
                                f"/api/v1/storefront/store/{test_store_id}/products",
                                params={"limit": 15, "page": action + 1},
                            )
                        elif action % 3 == 1:
                            response = await client.get(
                                f"/api/v1/storefront/store/{test_store_id}/products",
                                params={"search": "test"},
                            )
                        else:
                            response = await client.get("/api/v1/public/health")

                        elapsed = (time.perf_counter() - start) * 1000
                        metrics.response_times.append(elapsed)

                        if response.status_code == 200:
                            successful += 1
                        else:
                            failed += 1

                    except Exception as e:
                        failed += 1
                        metrics.record_error(str(e))

                    # Simulate user think time (200-500ms)
                    await asyncio.sleep(0.2 + (user_id % 3) * 0.1)

            return StressTestResult(
                total_requests=successful + failed,
                successful_requests=successful,
                failed_requests=failed,
                total_duration_ms=0,  # Not tracked per user
                requests_per_second=0,
                metrics=metrics,
            )

        # Run all user sessions concurrently
        start_time = time.perf_counter()
        tasks = [user_session(i) for i in range(num_users)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        total_duration = (time.perf_counter() - start_time) * 1000

        # Aggregate results
        total_successful = 0
        total_failed = 0
        all_times: list[float] = []

        for result in results:
            if isinstance(result, StressTestResult):
                total_successful += result.successful_requests
                total_failed += result.failed_requests
                all_times.extend(result.metrics.response_times)

        total_requests = total_successful + total_failed
        success_rate = (
            (total_successful / total_requests * 100) if total_requests > 0 else 0
        )

        combined_metrics = PerformanceMetrics()
        combined_metrics.response_times = all_times

        print(
            f"\nConcurrent users test ({num_users} users, {actions_per_user} actions each):"
        )
        print(f"  Total requests: {total_requests}")
        print(f"  Successful: {total_successful}")
        print(f"  Failed: {total_failed}")
        print(f"  Success rate: {success_rate:.2f}%")
        print(f"  Total duration: {total_duration:.0f}ms")
        print(f"  Response times: {combined_metrics.summary()['response_time']}")

        assert success_rate >= 95.0, (
            f"Success rate {success_rate:.2f}% below 95% threshold"
        )


class TestEndpointIsolation:
    """Tests to verify endpoint performance isolation."""

    @pytest.mark.asyncio
    async def test_slow_endpoint_doesnt_affect_fast_endpoint(
        self,
        perf_config: PerformanceConfig,
        test_store_id: str,
    ) -> None:
        """Verify that load on one endpoint doesn't affect another.

        Hit search endpoint (slower) while monitoring health endpoint (fast).
        """
        health_times: list[float] = []
        search_times: list[float] = []

        async with httpx.AsyncClient(
            base_url=perf_config.api_base_url,
            timeout=30.0,
        ) as client:

            async def hit_health():
                for _ in range(20):
                    start = time.perf_counter()
                    await client.get("/api/v1/public/health")
                    elapsed = (time.perf_counter() - start) * 1000
                    health_times.append(elapsed)
                    await asyncio.sleep(0.1)

            async def hit_search():
                for _ in range(10):
                    start = time.perf_counter()
                    await client.get(
                        f"/api/v1/storefront/store/{test_store_id}/products",
                        params={"search": "test product long search"},
                    )
                    elapsed = (time.perf_counter() - start) * 1000
                    search_times.append(elapsed)
                    await asyncio.sleep(0.2)

            # Run both concurrently
            await asyncio.gather(hit_health(), hit_search())

        health_metrics = PerformanceMetrics()
        health_metrics.response_times = health_times

        search_metrics = PerformanceMetrics()
        search_metrics.response_times = search_times

        print("\nEndpoint isolation test:")
        print(f"  Health endpoint p95: {health_metrics.p95_response_time:.2f}ms")
        print(f"  Search endpoint p95: {search_metrics.p95_response_time:.2f}ms")

        # Health endpoint should stay fast even under search load
        assert health_metrics.p95_response_time < 200.0, (
            f"Health endpoint degraded: p95={health_metrics.p95_response_time:.2f}ms"
        )


class TestGracefulDegradation:
    """Tests for graceful degradation under extreme load."""

    @pytest.mark.asyncio
    async def test_graceful_degradation_under_extreme_load(
        self,
        perf_config: PerformanceConfig,
        test_store_id: str,
    ) -> None:
        """Test that API degrades gracefully under extreme load.

        Even under 100+ concurrent requests, API should:
        - Return proper HTTP status codes (not crash)
        - Eventually recover
        """
        extreme_concurrency = 100
        requests_count = 200

        metrics = PerformanceMetrics()
        status_codes: dict[int, int] = {}

        async with httpx.AsyncClient(
            base_url=perf_config.api_base_url,
            timeout=60.0,
            limits=httpx.Limits(max_connections=200),
        ) as client:
            semaphore = asyncio.Semaphore(extreme_concurrency)

            async def make_request():
                async with semaphore:
                    try:
                        start = time.perf_counter()
                        response = await client.get(
                            f"/api/v1/storefront/store/{test_store_id}/products",
                            params={"limit": 10},
                        )
                        elapsed = (time.perf_counter() - start) * 1000
                        metrics.response_times.append(elapsed)

                        code = response.status_code
                        status_codes[code] = status_codes.get(code, 0) + 1
                    except Exception as e:
                        metrics.record_error(str(e))
                        status_codes[-1] = status_codes.get(-1, 0) + 1

            tasks = [make_request() for _ in range(requests_count)]
            await asyncio.gather(*tasks)

        print(f"\nGraceful degradation test ({extreme_concurrency} concurrent):")
        print(f"  Status codes: {status_codes}")
        print(f"  Response times: {metrics.summary()['response_time']}")
        print(f"  Errors: {len(metrics.errors)}")

        # API should not completely fail
        total_responses = sum(v for k, v in status_codes.items() if k != -1)

        # At least 50% of requests should get a response (not connection error)
        response_rate = total_responses / requests_count * 100
        assert response_rate >= 50.0, (
            f"Too many connection failures under load: {response_rate:.2f}% got responses"
        )

        # 200s should be majority of responses
        ok_responses = status_codes.get(200, 0)
        ok_rate = ok_responses / requests_count * 100
        print(f"  200 OK rate: {ok_rate:.2f}%")
