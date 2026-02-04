"""Tests for security headers middleware.

These tests verify that all required security headers are present
in HTTP responses and have correct values.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.security_headers import (
    SecurityHeadersMiddleware,
    get_security_headers_middleware,
)


@pytest.fixture
def app_with_security_headers() -> FastAPI:
    """Create a test app with security headers middleware."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/test")
    async def test_endpoint():
        return {"message": "test"}

    @app.get("/api/data")
    async def api_endpoint():
        return {"data": [1, 2, 3]}

    return app


@pytest.fixture
def client(app_with_security_headers: FastAPI) -> TestClient:
    """Create a test client for the app."""
    return TestClient(app_with_security_headers)


class TestSecurityHeadersPresence:
    """Tests to verify all security headers are present."""

    def test_x_frame_options_header_present(self, client: TestClient):
        """Test that X-Frame-Options header is present."""
        response = client.get("/test")

        assert "X-Frame-Options" in response.headers
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_x_content_type_options_header_present(self, client: TestClient):
        """Test that X-Content-Type-Options header is present."""
        response = client.get("/test")

        assert "X-Content-Type-Options" in response.headers
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_strict_transport_security_header_present(self, client: TestClient):
        """Test that Strict-Transport-Security header is present."""
        response = client.get("/test")

        assert "Strict-Transport-Security" in response.headers
        hsts = response.headers["Strict-Transport-Security"]
        assert "max-age=" in hsts
        assert "includeSubDomains" in hsts

    def test_content_security_policy_header_present(self, client: TestClient):
        """Test that Content-Security-Policy header is present."""
        response = client.get("/test")

        assert "Content-Security-Policy" in response.headers
        csp = response.headers["Content-Security-Policy"]
        assert "default-src" in csp

    def test_x_xss_protection_header_present(self, client: TestClient):
        """Test that X-XSS-Protection header is present."""
        response = client.get("/test")

        assert "X-XSS-Protection" in response.headers
        assert response.headers["X-XSS-Protection"] == "1; mode=block"

    def test_referrer_policy_header_present(self, client: TestClient):
        """Test that Referrer-Policy header is present."""
        response = client.get("/test")

        assert "Referrer-Policy" in response.headers
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_permissions_policy_header_present(self, client: TestClient):
        """Test that Permissions-Policy header is present."""
        response = client.get("/test")

        assert "Permissions-Policy" in response.headers
        policy = response.headers["Permissions-Policy"]
        # Verify some common restricted features
        assert "camera=()" in policy
        assert "microphone=()" in policy
        assert "geolocation=()" in policy

    def test_cross_origin_opener_policy_header_present(self, client: TestClient):
        """Test that Cross-Origin-Opener-Policy header is present."""
        response = client.get("/test")

        assert "Cross-Origin-Opener-Policy" in response.headers
        assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"

    def test_cross_origin_resource_policy_header_present(self, client: TestClient):
        """Test that Cross-Origin-Resource-Policy header is present."""
        response = client.get("/test")

        assert "Cross-Origin-Resource-Policy" in response.headers
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"

    def test_x_permitted_cross_domain_policies_header_present(self, client: TestClient):
        """Test that X-Permitted-Cross-Domain-Policies header is present."""
        response = client.get("/test")

        assert "X-Permitted-Cross-Domain-Policies" in response.headers
        assert response.headers["X-Permitted-Cross-Domain-Policies"] == "none"


class TestSecurityHeadersValues:
    """Tests to verify security header values are correct."""

    def test_hsts_max_age_is_one_year(self, client: TestClient):
        """Test that HSTS max-age is at least 1 year (31536000 seconds)."""
        response = client.get("/test")

        hsts = response.headers["Strict-Transport-Security"]
        # Extract max-age value
        import re
        match = re.search(r"max-age=(\d+)", hsts)
        assert match is not None
        max_age = int(match.group(1))
        assert max_age >= 31536000  # At least 1 year

    def test_csp_has_required_directives(self, client: TestClient):
        """Test that CSP includes required security directives."""
        response = client.get("/test")

        csp = response.headers["Content-Security-Policy"]

        # Check for important directives
        assert "default-src" in csp
        assert "frame-ancestors" in csp
        assert "base-uri" in csp

    def test_x_frame_options_denies_framing(self, client: TestClient):
        """Test that X-Frame-Options prevents all framing."""
        response = client.get("/test")

        # Should be DENY (strictest) or at minimum SAMEORIGIN
        x_frame = response.headers["X-Frame-Options"]
        assert x_frame in ("DENY", "SAMEORIGIN")

    def test_cache_control_prevents_caching(self, client: TestClient):
        """Test that Cache-Control prevents storing sensitive data."""
        response = client.get("/test")

        # API responses should not be cached by default
        cache_control = response.headers.get("Cache-Control", "")
        assert "no-store" in cache_control or "private" in cache_control


class TestSecurityHeadersOnAllEndpoints:
    """Tests to verify headers are present on all endpoints."""

    def test_headers_on_root_endpoint(self, client: TestClient):
        """Test that security headers are on root endpoint."""
        response = client.get("/test")

        # Verify key headers are present
        assert "X-Frame-Options" in response.headers
        assert "Content-Security-Policy" in response.headers

    def test_headers_on_api_endpoint(self, client: TestClient):
        """Test that security headers are on API endpoints."""
        response = client.get("/api/data")

        # Verify key headers are present
        assert "X-Frame-Options" in response.headers
        assert "Content-Security-Policy" in response.headers

    def test_headers_on_404_response(self, client: TestClient):
        """Test that security headers are present even on 404 errors."""
        response = client.get("/nonexistent")

        # Verify key headers are present even on error responses
        assert "X-Frame-Options" in response.headers
        assert "X-Content-Type-Options" in response.headers


class TestSecurityHeadersConfiguration:
    """Tests for configurable security headers."""

    def test_custom_csp(self):
        """Test that custom CSP can be provided."""
        app = FastAPI()
        custom_csp = "default-src 'none'; script-src 'self'"

        class CustomSecurityMiddleware(SecurityHeadersMiddleware):
            def __init__(self, app_inner):
                super().__init__(app_inner, csp=custom_csp)

        app.add_middleware(CustomSecurityMiddleware)

        @app.get("/test")
        async def test_endpoint():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/test")

        assert response.headers["Content-Security-Policy"] == custom_csp

    def test_hsts_can_be_disabled(self):
        """Test that HSTS can be disabled (for development)."""
        app = FastAPI()

        class NoHSTSMiddleware(SecurityHeadersMiddleware):
            def __init__(self, app_inner):
                super().__init__(app_inner, include_hsts=False)

        app.add_middleware(NoHSTSMiddleware)

        @app.get("/test")
        async def test_endpoint():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/test")

        # HSTS should not be present when disabled
        assert "Strict-Transport-Security" not in response.headers

    def test_factory_function_creates_middleware(self):
        """Test that factory function creates configured middleware."""
        app = FastAPI()
        custom_csp = "default-src 'self'"

        MiddlewareClass = get_security_headers_middleware(
            csp=custom_csp,
            include_hsts=True,
        )
        app.add_middleware(MiddlewareClass)

        @app.get("/test")
        async def test_endpoint():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/test")

        assert response.headers["Content-Security-Policy"] == custom_csp


class TestClickjackingProtection:
    """Tests specifically for clickjacking protection."""

    def test_x_frame_options_blocks_iframe_embedding(self, client: TestClient):
        """Test that responses cannot be embedded in iframes."""
        response = client.get("/test")

        # X-Frame-Options: DENY prevents all framing
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_csp_frame_ancestors_blocks_framing(self, client: TestClient):
        """Test that CSP frame-ancestors also blocks framing."""
        response = client.get("/test")

        csp = response.headers["Content-Security-Policy"]
        # frame-ancestors 'none' is the CSP equivalent of X-Frame-Options: DENY
        assert "frame-ancestors" in csp


class TestMIMESniffingProtection:
    """Tests for MIME type sniffing protection."""

    def test_nosniff_prevents_mime_sniffing(self, client: TestClient):
        """Test that X-Content-Type-Options prevents MIME sniffing."""
        response = client.get("/test")

        # nosniff tells browser to trust Content-Type header
        assert response.headers["X-Content-Type-Options"] == "nosniff"
