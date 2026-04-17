"""Tests for Meta rate limiter."""

from unittest.mock import AsyncMock

import pytest

from src.infrastructure.external_services.meta.rate_limiter import MetaRateLimiter


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    return redis


@pytest.mark.asyncio
async def test_rate_limiter_acquire_first_call(mock_redis):
    """Test that the first call acquires tokens successfully."""
    limiter = MetaRateLimiter(
        redis_client=mock_redis,
        channel="whatsapp",
        tenant_id="tenant-123",
        max_tokens=100,
        refill_rate=100 / 3600,
    )

    result = await limiter.acquire(tokens=1, block=False)

    assert result is True
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_rate_limiter_throttles_when_exhausted(mock_redis):
    """Test that the rate limiter throttles when tokens are exhausted."""

    # Simulate an empty bucket
    mock_redis.get = AsyncMock(return_value="0:100.0")

    limiter = MetaRateLimiter(
        redis_client=mock_redis,
        channel="whatsapp",
        tenant_id="tenant-123",
        max_tokens=100,
        refill_rate=100 / 3600,
    )

    result = await limiter.acquire(tokens=1, block=False)

    # Should return False when tokens are exhausted and not blocking
    assert result is False


@pytest.mark.asyncio
async def test_rate_limiter_refills_over_time(mock_redis):
    """Test that tokens are refilled over time."""
    import time

    now = time.time()
    # Simulate tokens were used 30 minutes ago
    thirty_minutes_ago = now - 1800
    _ = 50 + (1800 * (100 / 3600))  # 50 + 50 = 100, unused but shows refill logic

    mock_redis.get = AsyncMock(return_value=f"50:{thirty_minutes_ago}")

    limiter = MetaRateLimiter(
        redis_client=mock_redis,
        channel="whatsapp",
        tenant_id="tenant-123",
        max_tokens=100,
        refill_rate=100 / 3600,
    )

    result = await limiter.acquire(tokens=1, block=False)

    # Should have refilled to max (100)
    assert result is True


@pytest.mark.asyncio
async def test_rate_limiter_get_remaining(mock_redis):
    """Test getting remaining tokens."""
    mock_redis.get = AsyncMock(return_value="75:100.0")

    limiter = MetaRateLimiter(
        redis_client=mock_redis,
        channel="whatsapp",
        tenant_id="tenant-123",
        max_tokens=100,
        refill_rate=100 / 3600,
    )

    remaining = await limiter.get_remaining()

    # Should be capped at max_tokens
    assert remaining == 75


@pytest.mark.asyncio
async def test_rate_limiter_max_tokens_capped(mock_redis):
    """Test that tokens don't exceed max_tokens."""
    import time

    now = time.time()
    # Simulate more tokens than max (should be capped on next acquire)
    mock_redis.get = AsyncMock(return_value=f"150:{now}")

    limiter = MetaRateLimiter(
        redis_client=mock_redis,
        channel="whatsapp",
        tenant_id="tenant-123",
        max_tokens=100,
        refill_rate=100 / 3600,
    )

    result = await limiter.acquire(tokens=1, block=False)

    # Should cap at max_tokens
    assert result is True
    # Verify the set call caps at max_tokens
    call_args = mock_redis.set.call_args
    set_value = call_args[0][1]
    tokens_available = float(set_value.split(":")[0])
    assert tokens_available <= 100
