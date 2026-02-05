"""Performance test fixtures and utilities.

This module provides fixtures for simulating network conditions
and measuring API performance under various scenarios.
"""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio


@dataclass
class NetworkProfile:
    """Network condition profile for simulation.

    Attributes:
        name: Human-readable profile name
        download_kbps: Download speed in kilobits per second
        upload_kbps: Upload speed in kilobits per second
        latency_ms: Network latency in milliseconds
        jitter_ms: Latency variation in milliseconds
        packet_loss_percent: Percentage of packet loss
    """

    name: str
    download_kbps: int
    upload_kbps: int
    latency_ms: int
    jitter_ms: int
    packet_loss_percent: float


# Standard network profiles for testing
NETWORK_PROFILES = {
    "3g_slow": NetworkProfile(
        name="Slow 3G",
        download_kbps=500,
        upload_kbps=250,
        latency_ms=400,
        jitter_ms=100,
        packet_loss_percent=2.0,
    ),
    "3g_regular": NetworkProfile(
        name="Regular 3G",
        download_kbps=1000,
        upload_kbps=500,
        latency_ms=200,
        jitter_ms=50,
        packet_loss_percent=0.5,
    ),
    "3g_fast": NetworkProfile(
        name="Fast 3G (HSPA+)",
        download_kbps=2000,
        upload_kbps=1000,
        latency_ms=100,
        jitter_ms=20,
        packet_loss_percent=0.1,
    ),
    "4g": NetworkProfile(
        name="4G LTE",
        download_kbps=10000,
        upload_kbps=5000,
        latency_ms=50,
        jitter_ms=10,
        packet_loss_percent=0.01,
    ),
    "wifi": NetworkProfile(
        name="WiFi",
        download_kbps=50000,
        upload_kbps=25000,
        latency_ms=10,
        jitter_ms=5,
        packet_loss_percent=0.0,
    ),
}

# Performance thresholds by network type
PERFORMANCE_THRESHOLDS = {
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
    "4g": {
        "p95_max_ms": 1000,
        "p99_max_ms": 2000,
        "max_response_bytes": 200000,
    },
    "wifi": {
        "p95_max_ms": 500,
        "p99_max_ms": 1000,
        "max_response_bytes": 500000,
    },
}


class NetworkSimulator:
    """Simulates network conditions by adding artificial latency.

    This is a software-only simulation that adds delays to requests
    to approximate real network conditions. For true network throttling,
    use system-level tools like tcconfig.
    """

    def __init__(self, profile: NetworkProfile):
        self.profile = profile

    async def simulate_latency(self) -> None:
        """Add simulated network latency to the current request."""
        # Base latency with random jitter
        import random

        jitter = random.uniform(-self.profile.jitter_ms, self.profile.jitter_ms)
        total_latency = max(0, self.profile.latency_ms + jitter)

        await asyncio.sleep(total_latency / 1000)

    def calculate_transfer_time(self, bytes_count: int) -> float:
        """Calculate transfer time in milliseconds for given bytes.

        Args:
            bytes_count: Number of bytes to transfer

        Returns:
            Transfer time in milliseconds
        """
        bits = bytes_count * 8
        kbps = self.profile.download_kbps
        return (bits / kbps) * 1000


class PerformanceValidator:
    """Validates API performance against thresholds."""

    def __init__(self):
        self.response_times: list[float] = []

    def record(self, response_time_ms: float) -> None:
        """Record a response time measurement."""
        self.response_times.append(response_time_ms)

    def get_percentile(self, percentile: int) -> float:
        """Calculate percentile of recorded response times.

        Args:
            percentile: Percentile to calculate (0-100)

        Returns:
            Response time at given percentile
        """
        if not self.response_times:
            return 0.0

        sorted_times = sorted(self.response_times)
        index = int(len(sorted_times) * (percentile / 100))
        index = min(index, len(sorted_times) - 1)

        return sorted_times[index]

    def validate(self, profile_name: str) -> None:
        """Validate recorded response times against thresholds.

        Args:
            profile_name: Network profile name for threshold lookup

        Raises:
            AssertionError: If thresholds are exceeded
        """
        if not self.response_times:
            return

        thresholds = PERFORMANCE_THRESHOLDS.get(profile_name, {})
        p95_max = thresholds.get("p95_max_ms", float("inf"))
        p99_max = thresholds.get("p99_max_ms", float("inf"))

        p95 = self.get_percentile(95)
        p99 = self.get_percentile(99)

        assert p95 <= p95_max, (
            f"P95 response time ({p95:.0f}ms) exceeds threshold "
            f"({p95_max}ms) for {profile_name}"
        )
        assert p99 <= p99_max, (
            f"P99 response time ({p99:.0f}ms) exceeds threshold "
            f"({p99_max}ms) for {profile_name}"
        )


# Fixtures


@pytest.fixture
def network_3g_slow() -> NetworkSimulator:
    """Fixture for slow 3G network simulation."""
    return NetworkSimulator(NETWORK_PROFILES["3g_slow"])


@pytest.fixture
def network_3g_regular() -> NetworkSimulator:
    """Fixture for regular 3G network simulation."""
    return NetworkSimulator(NETWORK_PROFILES["3g_regular"])


@pytest.fixture
def network_3g_fast() -> NetworkSimulator:
    """Fixture for fast 3G (HSPA+) network simulation."""
    return NetworkSimulator(NETWORK_PROFILES["3g_fast"])


@pytest.fixture
def performance_validator() -> PerformanceValidator:
    """Fixture to validate performance against thresholds."""
    return PerformanceValidator()


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client fixture for performance tests."""
    async with httpx.AsyncClient(
        base_url="http://localhost:8000",
        timeout=30.0,
    ) as client:
        yield client


@pytest.fixture
def api_base_url() -> str:
    """Base URL for API tests."""
    import os

    return os.getenv("API_URL", "http://localhost:8000")


@pytest.fixture
def test_store_id() -> str:
    """Test store ID for API requests."""
    import os

    return os.getenv("TEST_STORE_ID", "00000000-0000-0000-0000-000000000001")
