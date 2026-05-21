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


# =============================================================================
# 3G Network Simulation
# =============================================================================


@dataclass
class NetworkProfile:
    """Network profile for simulating different connection types."""

    latency_ms: float
    download_kbps: float
    upload_kbps: float
    packet_loss_percent: float = 0.0


NETWORK_PROFILES: dict[str, NetworkProfile] = {
    "3g_slow": NetworkProfile(
        latency_ms=400, download_kbps=500, upload_kbps=250, packet_loss_percent=1.0
    ),
    "3g_regular": NetworkProfile(
        latency_ms=200, download_kbps=1000, upload_kbps=500, packet_loss_percent=0.5
    ),
    "3g_fast": NetworkProfile(
        latency_ms=100, download_kbps=2000, upload_kbps=1000, packet_loss_percent=0.1
    ),
}


PERFORMANCE_THRESHOLDS: dict[str, dict[str, float]] = {
    "3g_slow": {
        "p95_max_ms": 5000,
        "p99_max_ms": 8000,
        "max_response_bytes": 50000,
    },
    "3g_regular": {
        "p95_max_ms": 3000,
        "p99_max_ms": 5000,
        "max_response_bytes": 50000,
    },
    "3g_fast": {
        "p95_max_ms": 2000,
        "p99_max_ms": 3000,
        "max_response_bytes": 100000,
    },
}


class NetworkSimulator:
    """Simulates network conditions for performance testing."""

    def __init__(self, profile: NetworkProfile) -> None:
        self.profile = profile

    async def simulate_latency(self) -> None:
        """Simulate network latency."""
        await asyncio.sleep(self.profile.latency_ms / 1000)

    def calculate_transfer_time(self, size_bytes: int) -> float:
        """Calculate transfer time in ms for a given payload size."""
        size_kb = size_bytes / 1024
        return (size_kb / self.profile.download_kbps) * 1000


class PerformanceValidator:
    """Collects response times and validates against thresholds."""

    def __init__(self) -> None:
        self.response_times: list[float] = []

    def record(self, time_ms: float) -> None:
        self.response_times.append(time_ms)

    def validate(self, profile_name: str) -> None:
        thresholds = PERFORMANCE_THRESHOLDS[profile_name]
        sorted_times = sorted(self.response_times)
        p95_idx = int(len(sorted_times) * 0.95)
        p99_idx = int(len(sorted_times) * 0.99)
        p95 = sorted_times[min(p95_idx, len(sorted_times) - 1)]
        p99 = sorted_times[min(p99_idx, len(sorted_times) - 1)]
        assert p95 <= thresholds["p95_max_ms"], (
            f"p95 ({p95:.0f}ms) exceeds {profile_name} threshold ({thresholds['p95_max_ms']}ms)"
        )
        assert p99 <= thresholds["p99_max_ms"], (
            f"p99 ({p99:.0f}ms) exceeds {profile_name} threshold ({thresholds['p99_max_ms']}ms)"
        )


# Fixtures for 3G simulation


@pytest.fixture
def network_3g_slow() -> NetworkSimulator:
    return NetworkSimulator(NETWORK_PROFILES["3g_slow"])


@pytest.fixture
def network_3g_regular() -> NetworkSimulator:
    return NetworkSimulator(NETWORK_PROFILES["3g_regular"])


@pytest.fixture
def network_3g_fast() -> NetworkSimulator:
    return NetworkSimulator(NETWORK_PROFILES["3g_fast"])


@pytest.fixture
def performance_validator() -> PerformanceValidator:
    return PerformanceValidator()
