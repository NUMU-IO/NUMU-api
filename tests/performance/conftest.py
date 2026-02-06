"""Shared fixtures for performance tests.

This module provides:
- Database session fixtures
- API test client fixtures
- Benchmark configuration helpers
- Performance metrics utilities
"""

import asyncio
import os
import statistics
import time
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# =============================================================================
# Configuration
# =============================================================================


@dataclass
class PerformanceConfig:
    """Configuration for performance tests."""

    # API settings
    api_base_url: str = os.getenv("TEST_API_URL", "http://localhost:8000")
    api_timeout: float = 30.0

    # Database settings
    database_url: str = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/numu_test",
    )

    # Redis settings
    redis_url: str = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/1")

    # Benchmark settings
    warmup_iterations: int = 3
    benchmark_iterations: int = 10
    slow_threshold_ms: float = 500.0

    # Thresholds
    p95_threshold_ms: float = 2000.0
    p99_threshold_ms: float = 3000.0
    max_response_size_bytes: int = 50_000


@dataclass
class PerformanceMetrics:
    """Collected performance metrics."""

    response_times: list[float] = field(default_factory=list)
    response_sizes: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def record_response(self, time_ms: float, size_bytes: int) -> None:
        """Record a response measurement."""
        self.response_times.append(time_ms)
        self.response_sizes.append(size_bytes)

    def record_error(self, error: str) -> None:
        """Record an error."""
        self.errors.append(error)

    @property
    def count(self) -> int:
        """Total number of recorded responses."""
        return len(self.response_times)

    @property
    def error_rate(self) -> float:
        """Error rate as a percentage."""
        total = self.count + len(self.errors)
        return (len(self.errors) / total * 100) if total > 0 else 0.0

    @property
    def avg_response_time(self) -> float:
        """Average response time in ms."""
        return statistics.mean(self.response_times) if self.response_times else 0.0

    @property
    def p50_response_time(self) -> float:
        """50th percentile response time in ms."""
        return statistics.median(self.response_times) if self.response_times else 0.0

    @property
    def p95_response_time(self) -> float:
        """95th percentile response time in ms."""
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def p99_response_time(self) -> float:
        """99th percentile response time in ms."""
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def min_response_time(self) -> float:
        """Minimum response time in ms."""
        return min(self.response_times) if self.response_times else 0.0

    @property
    def max_response_time(self) -> float:
        """Maximum response time in ms."""
        return max(self.response_times) if self.response_times else 0.0

    @property
    def avg_response_size(self) -> float:
        """Average response size in bytes."""
        return statistics.mean(self.response_sizes) if self.response_sizes else 0.0

    def summary(self) -> dict[str, Any]:
        """Get a summary of all metrics."""
        return {
            "count": self.count,
            "error_rate": f"{self.error_rate:.2f}%",
            "response_time": {
                "avg": f"{self.avg_response_time:.2f}ms",
                "p50": f"{self.p50_response_time:.2f}ms",
                "p95": f"{self.p95_response_time:.2f}ms",
                "p99": f"{self.p99_response_time:.2f}ms",
                "min": f"{self.min_response_time:.2f}ms",
                "max": f"{self.max_response_time:.2f}ms",
            },
            "response_size": {
                "avg": f"{self.avg_response_size:.0f} bytes",
            },
            "errors": len(self.errors),
        }


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def perf_config() -> PerformanceConfig:
    """Performance test configuration."""
    return PerformanceConfig()


@pytest.fixture
def perf_metrics() -> PerformanceMetrics:
    """Fresh performance metrics for each test."""
    return PerformanceMetrics()


@pytest_asyncio.fixture
async def async_client(
    perf_config: PerformanceConfig,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client for API tests."""
    async with httpx.AsyncClient(
        base_url=perf_config.api_base_url,
        timeout=perf_config.api_timeout,
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="session")
async def db_engine(perf_config: PerformanceConfig):
    """Create async database engine for tests."""
    engine = create_async_engine(
        perf_config.database_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create async database session for tests."""
    async_session_maker = sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
def test_store_id() -> str:
    """Test store ID for API requests."""
    return os.getenv("TEST_STORE_ID", "00000000-0000-0000-0000-000000000001")


@pytest.fixture
def api_base_url(perf_config: PerformanceConfig) -> str:
    """API base URL."""
    return perf_config.api_base_url


# =============================================================================
# Helper Functions
# =============================================================================


class TimedRequest:
    """Context manager for timing HTTP requests."""

    def __init__(self) -> None:
        self.start_time: float = 0.0
        self.end_time: float = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> "TimedRequest":
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, *args) -> None:
        self.end_time = time.perf_counter()
        self.elapsed_ms = (self.end_time - self.start_time) * 1000


async def measure_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    metrics: PerformanceMetrics,
    **kwargs,
) -> httpx.Response | None:
    """Measure and record an HTTP request."""
    timer = TimedRequest()
    try:
        with timer:
            response = await client.request(method, url, **kwargs)
        metrics.record_response(timer.elapsed_ms, len(response.content))
        return response
    except Exception as e:
        metrics.record_error(str(e))
        return None


async def run_benchmark(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    iterations: int = 10,
    warmup: int = 3,
    **kwargs,
) -> PerformanceMetrics:
    """Run a benchmark with warmup iterations."""
    metrics = PerformanceMetrics()

    # Warmup
    for _ in range(warmup):
        try:
            await client.request(method, url, **kwargs)
        except Exception:
            pass

    # Benchmark
    for _ in range(iterations):
        await measure_request(client, method, url, metrics, **kwargs)

    return metrics


def assert_performance(
    metrics: PerformanceMetrics,
    p95_max_ms: float = 2000.0,
    p99_max_ms: float = 3000.0,
    max_error_rate: float = 1.0,
    max_response_size: int | None = None,
) -> None:
    """Assert performance metrics meet thresholds."""
    assert metrics.p95_response_time <= p95_max_ms, (
        f"p95 response time {metrics.p95_response_time:.2f}ms exceeds "
        f"threshold {p95_max_ms}ms"
    )

    assert metrics.p99_response_time <= p99_max_ms, (
        f"p99 response time {metrics.p99_response_time:.2f}ms exceeds "
        f"threshold {p99_max_ms}ms"
    )

    assert metrics.error_rate <= max_error_rate, (
        f"Error rate {metrics.error_rate:.2f}% exceeds threshold {max_error_rate}%"
    )

    if max_response_size is not None:
        assert metrics.avg_response_size <= max_response_size, (
            f"Average response size {metrics.avg_response_size:.0f} bytes exceeds "
            f"threshold {max_response_size} bytes"
        )
