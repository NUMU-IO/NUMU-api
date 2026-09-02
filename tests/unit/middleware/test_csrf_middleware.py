"""Unit tests for CSRFMiddleware.

Regression cover for the storefront BFF exemption: the Next.js storefront
proxies to the backend server-to-server, forwarding the customer's cookies
but never the backend's ``X-CSRF-Token``. A logged-in customer's domain-wide
``customer_access_token`` trips ``has_cookie_auth`` and used to 403 every
proxied write ("CSRF validation failed") — first observed at the checkout
shipping step. These tests pin the fix: the whole ``/storefront/*`` proxied
surface is exempt, while non-storefront authed routes still enforce CSRF.
"""

from unittest.mock import MagicMock

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.api.middleware.csrf import CSRFMiddleware

STORE = "11111111-1111-1111-1111-111111111111"
_PASSED = Response("ok", status_code=200)  # sentinel: middleware called next


async def _call_next(_request):
    return _PASSED


def _make_request(
    path: str,
    method: str = "POST",
    cookie: str | None = "customer_access_token=abc",
    csrf_header: str | None = None,
) -> Request:
    """Build a real Starlette Request from an ASGI scope so the middleware
    reads genuine ``request.cookies`` / ``request.headers``."""
    headers: list[tuple[bytes, bytes]] = []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if csrf_header:
        headers.append((b"x-csrf-token", csrf_header.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "scheme": "https",
        "server": ("numueg.app", 443),
    }
    return Request(scope)


async def _dispatch(request: Request):
    return await CSRFMiddleware(app=MagicMock()).dispatch(request, _call_next)


def _passed(response) -> bool:
    return response is _PASSED


def _blocked_403(response) -> bool:
    return isinstance(response, JSONResponse) and response.status_code == 403


class TestCSRFStorefrontBffExemption:
    """The BFF-proxied storefront surface must skip CSRF even for a
    logged-in customer that carries no ``X-CSRF-Token`` header."""

    # Every storefront write a logged-in customer can trigger via the proxy.
    PROXIED_ROUTES = [
        f"/api/v1/storefront/store/{STORE}/shipping/options",  # the reported bug
        f"/api/v1/storefront/store/{STORE}/shipping/quote",
        f"/api/v1/storefront/store/{STORE}/checkout",
        f"/api/v1/storefront/store/{STORE}/pay/order-123",
        f"/api/v1/storefront/store/{STORE}/cart/discounts",
        f"/api/v1/storefront/store/{STORE}/coupon/validate",
        f"/api/v1/storefront/store/{STORE}/promotions/p1/apply",
        f"/api/v1/storefront/store/{STORE}/products/pr1/reviews",
        "/api/v1/storefront/me/addresses",
        "/api/v1/storefront/me/password",
        "/api/v1/storefront/cart/items",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", PROXIED_ROUTES)
    async def test_logged_in_customer_without_csrf_header_passes(self, path):
        """Customer cookie present, no X-CSRF-Token — must NOT 403."""
        response = await _dispatch(_make_request(path))
        assert _passed(response), f"{path} should skip CSRF but was blocked"

    @pytest.mark.asyncio
    async def test_guest_storefront_write_passes(self):
        """A guest (no auth cookie) was never CSRF-gated; keep it that way."""
        request = _make_request(
            f"/api/v1/storefront/store/{STORE}/checkout", cookie=None
        )
        assert _passed(await _dispatch(request))

    @pytest.mark.asyncio
    async def test_reported_shipping_options_regression(self):
        """Exact reproduction of the reported 403 on shipping/options."""
        request = _make_request(
            f"/api/v1/storefront/store/{STORE}/shipping/options",
            cookie="customer_access_token=abc; customer_refresh_token=def",
        )
        assert not _blocked_403(await _dispatch(request))


class TestCSRFStillEnforcedElsewhere:
    """Non-storefront authed routes must keep the double-submit protection."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path,method",
        [
            ("/api/v1/admin/platform-settings", "PATCH"),
            ("/api/v1/products/p1", "PATCH"),
            ("/api/v1/orders", "POST"),
        ],
    )
    async def test_authed_write_without_token_is_403(self, path, method):
        request = _make_request(path, method=method, cookie="access_token=x")
        assert _blocked_403(await _dispatch(request))

    @pytest.mark.asyncio
    async def test_matching_double_submit_passes(self):
        request = _make_request(
            "/api/v1/admin/platform-settings",
            method="PATCH",
            cookie="access_token=x; csrf_token=tok",
            csrf_header="tok",
        )
        assert _passed(await _dispatch(request))

    @pytest.mark.asyncio
    async def test_mismatched_double_submit_is_403(self):
        request = _make_request(
            "/api/v1/admin/platform-settings",
            method="PATCH",
            cookie="access_token=x; csrf_token=tok",
            csrf_header="different",
        )
        assert _blocked_403(await _dispatch(request))

    @pytest.mark.asyncio
    async def test_unauthenticated_write_passes_through(self):
        """No auth cookie => not token-fished, just passed to the route."""
        request = _make_request("/api/v1/orders", cookie=None)
        assert _passed(await _dispatch(request))

    @pytest.mark.asyncio
    async def test_safe_method_skips_csrf(self):
        request = _make_request(
            "/api/v1/admin/platform-settings", method="GET", cookie="access_token=x"
        )
        assert _passed(await _dispatch(request))
