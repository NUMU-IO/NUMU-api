"""Redis cache performance tests.

These tests verify that caching provides expected performance improvements.

Usage:
    pytest tests/performance/test_cache_performance.py -v -s
"""

import asyncio
import json
import os
import time
import uuid

import pytest
import pytest_asyncio

from tests.performance.conftest import PerformanceMetrics

# Try to import redis, skip tests if not available
try:
    import redis.asyncio as aioredis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


@pytest.fixture
def redis_url() -> str:
    """Redis connection URL."""
    return os.getenv("TEST_REDIS_URL", "redis://localhost:6379/1")


@pytest_asyncio.fixture
async def redis_client(redis_url: str):
    """Async Redis client fixture."""
    if not REDIS_AVAILABLE:
        pytest.skip("redis package not installed")

    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
        yield client
    except Exception:
        pytest.skip("Redis not available")
    finally:
        await client.aclose()


class TestCacheBasicPerformance:
    """Basic cache operation performance tests."""

    @pytest.mark.asyncio
    async def test_cache_read_performance(self, redis_client) -> None:
        """Test cache read performance.

        Expected: <5ms for single key read
        """
        metrics = PerformanceMetrics()
        test_key = "perf:test:read"
        test_value = json.dumps({"id": "123", "name": "Test Product"})

        # Set up test data
        await redis_client.set(test_key, test_value)

        try:
            for _ in range(50):
                start = time.perf_counter()
                _ = await redis_client.get(test_key)
                elapsed_ms = (time.perf_counter() - start) * 1000
                metrics.response_times.append(elapsed_ms)

            assert metrics.p95_response_time < 5.0, (
                f"Cache read too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nCache read performance: {metrics.summary()}")
        finally:
            await redis_client.delete(test_key)

    @pytest.mark.asyncio
    async def test_cache_write_performance(self, redis_client) -> None:
        """Test cache write performance.

        Expected: <10ms for single key write
        """
        metrics = PerformanceMetrics()
        test_data = json.dumps({
            "id": "123",
            "name": "Test Product",
            "description": "A test product with some description text.",
            "price": "99.99",
            "images": ["img1.jpg", "img2.jpg"],
        })

        keys_to_cleanup = []
        try:
            for i in range(50):
                key = f"perf:test:write:{i}"
                keys_to_cleanup.append(key)

                start = time.perf_counter()
                await redis_client.set(key, test_data, ex=60)
                elapsed_ms = (time.perf_counter() - start) * 1000
                metrics.response_times.append(elapsed_ms)

            assert metrics.p95_response_time < 10.0, (
                f"Cache write too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nCache write performance: {metrics.summary()}")
        finally:
            if keys_to_cleanup:
                await redis_client.delete(*keys_to_cleanup)

    @pytest.mark.asyncio
    async def test_cache_mget_performance(self, redis_client) -> None:
        """Test multi-key read performance.

        Expected: <20ms for 20 keys
        """
        metrics = PerformanceMetrics()
        num_keys = 20
        test_keys = [f"perf:test:mget:{i}" for i in range(num_keys)]
        test_value = json.dumps({"id": "123", "name": "Test"})

        # Set up test data
        for key in test_keys:
            await redis_client.set(key, test_value)

        try:
            for _ in range(20):
                start = time.perf_counter()
                _ = await redis_client.mget(test_keys)
                elapsed_ms = (time.perf_counter() - start) * 1000
                metrics.response_times.append(elapsed_ms)

            assert metrics.p95_response_time < 20.0, (
                f"Cache mget too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nCache mget ({num_keys} keys): {metrics.summary()}")
        finally:
            await redis_client.delete(*test_keys)


class TestCachePatternPerformance:
    """Tests for common cache pattern performance."""

    @pytest.mark.asyncio
    async def test_product_list_cache_pattern(self, redis_client) -> None:
        """Test product list caching pattern performance.

        Simulates caching a paginated product list.
        """
        metrics = PerformanceMetrics()

        # Simulate product list data (~20 items)
        products = [
            {
                "id": str(uuid.uuid4()),
                "name": f"Product {i}",
                "slug": f"product-{i}",
                "price": f"{99.99 + i}",
                "images": [f"img{i}.jpg"],
            }
            for i in range(20)
        ]
        cache_value = json.dumps({
            "items": products,
            "total": 100,
            "page": 1,
            "page_size": 20,
        })

        cache_key = "perf:products:store:123:page:1:limit:20"
        await redis_client.set(cache_key, cache_value, ex=300)

        try:
            for _ in range(30):
                start = time.perf_counter()
                raw = await redis_client.get(cache_key)
                if raw:
                    _ = json.loads(raw)
                elapsed_ms = (time.perf_counter() - start) * 1000
                metrics.response_times.append(elapsed_ms)

            assert metrics.p95_response_time < 10.0, (
                f"Product list cache read too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nProduct list cache pattern: {metrics.summary()}")
        finally:
            await redis_client.delete(cache_key)

    @pytest.mark.asyncio
    async def test_cache_invalidation_pattern(self, redis_client) -> None:
        """Test cache invalidation by prefix pattern.

        Simulates invalidating all cached products for a store.
        """
        metrics = PerformanceMetrics()
        store_id = "store123"
        prefix = f"perf:products:{store_id}:*"

        # Set up multiple cache entries
        keys = []
        for i in range(50):
            key = f"perf:products:{store_id}:page:{i}"
            keys.append(key)
            await redis_client.set(key, f"data{i}", ex=300)

        try:
            for _ in range(10):
                start = time.perf_counter()

                # Scan and delete pattern
                cursor = 0
                deleted_keys = []
                while True:
                    cursor, found_keys = await redis_client.scan(
                        cursor=cursor,
                        match=prefix,
                        count=100,
                    )
                    if found_keys:
                        deleted_keys.extend(found_keys)
                    if cursor == 0:
                        break

                if deleted_keys:
                    await redis_client.delete(*deleted_keys)

                elapsed_ms = (time.perf_counter() - start) * 1000
                metrics.response_times.append(elapsed_ms)

                # Re-create keys for next iteration
                for key in keys:
                    await redis_client.set(key, "data", ex=300)

            print(f"\nCache invalidation (50 keys): {metrics.summary()}")
            # Invalidation can be slower, but should be < 100ms
            assert metrics.p95_response_time < 100.0, (
                f"Cache invalidation too slow: p95={metrics.p95_response_time:.2f}ms"
            )
        finally:
            # Cleanup
            for key in keys:
                await redis_client.delete(key)


class TestCacheVsDatabaseComparison:
    """Tests comparing cache vs database performance."""

    @pytest.mark.asyncio
    async def test_cache_speedup_factor(self, redis_client) -> None:
        """Measure expected speedup from caching.

        Cache should be 10x+ faster than typical DB query.
        """
        # Simulate "DB query" with sleep
        db_latency_ms = 50  # Typical DB query latency

        cache_metrics = PerformanceMetrics()
        cache_key = "perf:speedup:test"
        cache_value = json.dumps({"data": "test" * 100})

        await redis_client.set(cache_key, cache_value)

        try:
            for _ in range(20):
                start = time.perf_counter()
                raw = await redis_client.get(cache_key)
                if raw:
                    _ = json.loads(raw)
                elapsed_ms = (time.perf_counter() - start) * 1000
                cache_metrics.response_times.append(elapsed_ms)

            cache_avg = cache_metrics.avg_response_time
            speedup = db_latency_ms / cache_avg if cache_avg > 0 else 0

            print("\nCache speedup analysis:")
            print(f"  Simulated DB latency: {db_latency_ms}ms")
            print(f"  Cache latency (avg): {cache_avg:.2f}ms")
            print(f"  Speedup factor: {speedup:.1f}x")

            # Cache should provide at least 5x speedup
            assert speedup > 5, (
                f"Cache speedup ({speedup:.1f}x) is less than expected (5x)"
            )
        finally:
            await redis_client.delete(cache_key)


class TestCacheConcurrency:
    """Tests for cache performance under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_cache_reads(self, redis_client) -> None:
        """Test cache performance under concurrent read load."""
        cache_key = "perf:concurrent:read"
        cache_value = json.dumps({"data": "test" * 50})

        await redis_client.set(cache_key, cache_value)

        all_times: list[float] = []

        async def reader():
            times = []
            for _ in range(20):
                start = time.perf_counter()
                _ = await redis_client.get(cache_key)
                elapsed = (time.perf_counter() - start) * 1000
                times.append(elapsed)
            return times

        try:
            # 10 concurrent readers
            tasks = [reader() for _ in range(10)]
            results = await asyncio.gather(*tasks)

            for times in results:
                all_times.extend(times)

            metrics = PerformanceMetrics()
            metrics.response_times = all_times

            print(f"\nConcurrent cache reads (10 readers): {metrics.summary()}")
            assert metrics.p95_response_time < 10.0, (
                f"Concurrent cache reads too slow: p95={metrics.p95_response_time:.2f}ms"
            )
        finally:
            await redis_client.delete(cache_key)

    @pytest.mark.asyncio
    async def test_cache_under_write_load(self, redis_client) -> None:
        """Test read performance while writes are happening."""
        read_key = "perf:mixed:read"
        await redis_client.set(read_key, "initial")

        read_times: list[float] = []
        write_keys: list[str] = []

        async def reader():
            times = []
            for _ in range(30):
                start = time.perf_counter()
                _ = await redis_client.get(read_key)
                elapsed = (time.perf_counter() - start) * 1000
                times.append(elapsed)
                await asyncio.sleep(0.01)
            return times

        async def writer():
            for i in range(30):
                key = f"perf:mixed:write:{i}"
                write_keys.append(key)
                await redis_client.set(key, f"data{i}", ex=60)
                await asyncio.sleep(0.01)

        try:
            read_task = asyncio.create_task(reader())
            write_task = asyncio.create_task(writer())

            times, _ = await asyncio.gather(read_task, write_task)
            read_times.extend(times)

            metrics = PerformanceMetrics()
            metrics.response_times = read_times

            print(f"\nCache reads during writes: {metrics.summary()}")
            # Reads should still be fast even with concurrent writes
            assert metrics.p95_response_time < 15.0, (
                f"Cache reads degraded during writes: p95={metrics.p95_response_time:.2f}ms"
            )
        finally:
            await redis_client.delete(read_key)
            if write_keys:
                await redis_client.delete(*write_keys)
