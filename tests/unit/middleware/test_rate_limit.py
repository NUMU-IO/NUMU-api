"""Unit tests for rate limiting middleware."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.middleware.rate_limit import (
    RateLimiter,
    RateLimitExceeded,
    RateLimitMiddleware,
    rate_limit_exceeded_handler,
)


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.limiter = RateLimiter()

    def test_is_allowed_first_request(self):
        """Test first request is always allowed."""
        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/products"
        mock_request.headers.get.return_value = None
        mock_request.client.host = "192.168.1.1"

        is_allowed, retry_after = self.limiter.is_allowed(
            mock_request, max_requests=10, window_seconds=60
        )

        assert is_allowed is True
        assert retry_after == 0

    def test_is_allowed_within_limit(self):
        """Test requests within limit are allowed."""
        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/products"
        mock_request.headers.get.return_value = None
        mock_request.client.host = "192.168.1.1"

        # Make 5 requests (limit is 10)
        for _ in range(5):
            is_allowed, retry_after = self.limiter.is_allowed(
                mock_request, max_requests=10, window_seconds=60
            )
            assert is_allowed is True
            assert retry_after == 0

    def test_is_allowed_exceeds_limit(self):
        """Test requests exceeding limit are denied."""
        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/products"
        mock_request.headers.get.return_value = None
        mock_request.client.host = "192.168.1.2"

        # Make max_requests requests
        for _ in range(5):
            self.limiter.is_allowed(mock_request, max_requests=5, window_seconds=60)

        # Next request should be denied
        is_allowed, retry_after = self.limiter.is_allowed(
            mock_request, max_requests=5, window_seconds=60
        )

        assert is_allowed is False
        assert retry_after > 0

    def test_is_allowed_different_paths_separate_limits(self):
        """Test different paths have separate rate limits."""
        mock_request1 = MagicMock()
        mock_request1.url.path = "/api/v1/products"
        mock_request1.headers.get.return_value = None
        mock_request1.client.host = "192.168.1.3"

        mock_request2 = MagicMock()
        mock_request2.url.path = "/api/v1/stores"
        mock_request2.headers.get.return_value = None
        mock_request2.client.host = "192.168.1.3"

        # Exhaust limit on products path
        for _ in range(3):
            self.limiter.is_allowed(mock_request1, max_requests=3, window_seconds=60)

        # Products path should be rate limited
        is_allowed1, _ = self.limiter.is_allowed(
            mock_request1, max_requests=3, window_seconds=60
        )
        assert is_allowed1 is False

        # Stores path should still be allowed
        is_allowed2, _ = self.limiter.is_allowed(
            mock_request2, max_requests=3, window_seconds=60
        )
        assert is_allowed2 is True

    def test_is_allowed_different_ips_separate_limits(self):
        """Test different IPs have separate rate limits."""
        mock_request1 = MagicMock()
        mock_request1.url.path = "/api/v1/products"
        mock_request1.headers.get.return_value = None
        mock_request1.client.host = "192.168.1.4"

        mock_request2 = MagicMock()
        mock_request2.url.path = "/api/v1/products"
        mock_request2.headers.get.return_value = None
        mock_request2.client.host = "192.168.1.5"

        # Exhaust limit for first IP
        for _ in range(3):
            self.limiter.is_allowed(mock_request1, max_requests=3, window_seconds=60)

        # First IP should be rate limited
        is_allowed1, _ = self.limiter.is_allowed(
            mock_request1, max_requests=3, window_seconds=60
        )
        assert is_allowed1 is False

        # Second IP should still be allowed
        is_allowed2, _ = self.limiter.is_allowed(
            mock_request2, max_requests=3, window_seconds=60
        )
        assert is_allowed2 is True

    def test_get_client_ip_from_x_forwarded_for(self):
        """Test IP extraction from X-Forwarded-For header."""
        mock_request = MagicMock()
        mock_request.headers.get.side_effect = lambda h: (
            "10.0.0.1, 10.0.0.2" if h == "X-Forwarded-For" else None
        )

        ip = self.limiter._get_client_ip(mock_request)
        assert ip == "10.0.0.1"

    def test_get_client_ip_from_x_real_ip(self):
        """Test IP extraction from X-Real-IP header."""
        mock_request = MagicMock()
        mock_request.headers.get.side_effect = lambda h: (
            "10.0.0.3" if h == "X-Real-IP" else None
        )

        ip = self.limiter._get_client_ip(mock_request)
        assert ip == "10.0.0.3"

    def test_get_client_ip_from_client_host(self):
        """Test IP extraction from request client."""
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None
        mock_request.client.host = "192.168.1.100"

        ip = self.limiter._get_client_ip(mock_request)
        assert ip == "192.168.1.100"

    def test_get_client_ip_no_client(self):
        """Test IP extraction when no client info available."""
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None
        mock_request.client = None

        ip = self.limiter._get_client_ip(mock_request)
        assert ip == "unknown"

    def test_cleanup_removes_old_entries(self):
        """Test cleanup removes old entries."""
        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/products"
        mock_request.headers.get.return_value = None
        mock_request.client.host = "192.168.1.6"

        # Add a request
        self.limiter.is_allowed(mock_request, max_requests=10, window_seconds=60)

        # Manually add old timestamp
        key = self.limiter._get_key(mock_request)
        self.limiter._requests[key].append(time.time() - 200)  # Old entry

        # Cleanup
        self.limiter.cleanup()

        # Old entry should be removed
        assert len(self.limiter._requests[key]) == 1


class TestRateLimitMiddleware:
    """Tests for RateLimitMiddleware class."""

    @pytest.mark.asyncio
    @patch("src.api.middleware.rate_limit.settings")
    async def test_dispatch_rate_limit_disabled(self, mock_settings):
        """Test middleware skips when rate limiting disabled."""
        mock_settings.rate_limit_enabled = False

        middleware = RateLimitMiddleware(app=MagicMock())
        mock_request = MagicMock()
        mock_response = MagicMock()
        mock_call_next = AsyncMock(return_value=mock_response)

        response = await middleware.dispatch(mock_request, mock_call_next)

        mock_call_next.assert_called_once_with(mock_request)
        assert response == mock_response

    @pytest.mark.asyncio
    @patch("src.api.middleware.rate_limit.settings")
    async def test_dispatch_skips_health_endpoint(self, mock_settings):
        """Test middleware skips health endpoints."""
        mock_settings.rate_limit_enabled = True

        middleware = RateLimitMiddleware(app=MagicMock())
        mock_request = MagicMock()
        mock_request.url.path = "/health"
        mock_response = MagicMock()
        mock_call_next = AsyncMock(return_value=mock_response)

        response = await middleware.dispatch(mock_request, mock_call_next)

        mock_call_next.assert_called_once_with(mock_request)
        assert response == mock_response

    @pytest.mark.asyncio
    @patch("src.api.middleware.rate_limit.settings")
    async def test_dispatch_skips_docs_endpoint(self, mock_settings):
        """Test middleware skips docs endpoints."""
        mock_settings.rate_limit_enabled = True

        middleware = RateLimitMiddleware(app=MagicMock())
        mock_request = MagicMock()
        mock_request.url.path = "/docs"
        mock_response = MagicMock()
        mock_call_next = AsyncMock(return_value=mock_response)

        response = await middleware.dispatch(mock_request, mock_call_next)

        mock_call_next.assert_called_once_with(mock_request)
        assert response == mock_response

    @pytest.mark.asyncio
    @patch("src.api.middleware.rate_limit.rate_limiter")
    @patch("src.api.middleware.rate_limit.settings")
    async def test_dispatch_adds_rate_limit_headers(
        self, mock_settings, mock_rate_limiter
    ):
        """Test middleware adds rate limit headers to response."""
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests_per_minute = 100
        mock_rate_limiter.is_allowed.return_value = (True, 0)
        mock_rate_limiter._get_key.return_value = "test_key"
        mock_rate_limiter._requests = {"test_key": [1, 2, 3]}

        middleware = RateLimitMiddleware(app=MagicMock())
        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/products"
        mock_response = MagicMock()
        mock_response.headers = {}
        mock_call_next = AsyncMock(return_value=mock_response)

        response = await middleware.dispatch(mock_request, mock_call_next)

        assert "X-RateLimit-Limit" in response.headers
        assert response.headers["X-RateLimit-Limit"] == "100"

    @pytest.mark.asyncio
    @patch("src.api.middleware.rate_limit.rate_limiter")
    @patch("src.api.middleware.rate_limit.settings")
    async def test_dispatch_returns_429_when_rate_limited(
        self, mock_settings, mock_rate_limiter
    ):
        """Test middleware returns 429 when rate limit exceeded."""
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests_per_minute = 100
        mock_rate_limiter.is_allowed.return_value = (False, 30)
        mock_rate_limiter._get_client_ip.return_value = "192.168.1.1"

        middleware = RateLimitMiddleware(app=MagicMock())
        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/products"
        mock_call_next = AsyncMock()

        response = await middleware.dispatch(mock_request, mock_call_next)

        assert response.status_code == 429
        mock_call_next.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.api.middleware.rate_limit.rate_limiter")
    @patch("src.api.middleware.rate_limit.settings")
    async def test_dispatch_uses_stricter_limit_for_auth(
        self, mock_settings, mock_rate_limiter
    ):
        """Test middleware uses stricter rate limit for auth endpoints."""
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_auth_requests_per_minute = 10
        mock_settings.rate_limit_requests_per_minute = 100
        mock_rate_limiter.is_allowed.return_value = (True, 0)
        mock_rate_limiter._get_key.return_value = "test_key"
        mock_rate_limiter._requests = {}

        middleware = RateLimitMiddleware(app=MagicMock())
        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/auth/login"
        mock_response = MagicMock()
        mock_response.headers = {}
        mock_call_next = AsyncMock(return_value=mock_response)

        await middleware.dispatch(mock_request, mock_call_next)

        # Verify auth endpoint uses stricter limit
        mock_rate_limiter.is_allowed.assert_called_once()
        call_args = mock_rate_limiter.is_allowed.call_args
        assert call_args.kwargs["max_requests"] == 10


class TestRateLimitExceeded:
    """Tests for RateLimitExceeded exception."""

    def test_exception_message(self):
        """Test exception contains retry_after in message."""
        exc = RateLimitExceeded(retry_after=30)

        assert exc.retry_after == 30
        assert "30" in str(exc)
        assert "Retry after" in str(exc)


class TestRateLimitExceededHandler:
    """Tests for rate_limit_exceeded_handler function."""

    def test_handler_returns_429_response(self):
        """Test handler returns 429 JSON response."""
        mock_request = MagicMock()
        exc = RateLimitExceeded(retry_after=45)

        response = rate_limit_exceeded_handler(mock_request, exc)

        assert response.status_code == 429
        assert "Retry-After" in response.headers
        assert response.headers["Retry-After"] == "45"
