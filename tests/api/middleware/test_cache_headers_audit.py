"""Cache-Control middleware audit tests.

These tests are the single most important guard in the load-test
remediation plan: if any of them ever fails, something has gotten
into the cacheable allowlist that varies per user, and Cloudflare
would happily cache one shopper's response and serve it to another.

The middleware is exercised against a minimal Starlette app rather
than the full NUMU FastAPI app. That keeps the tests independent of
the project's session-scoped asyncpg pool (which interacts badly
with pytest-asyncio on Windows — see Step 04 lessons) and means
``make test`` can run them with no Postgres / Redis configured.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.api.middleware.cache_headers import (
    _CACHEABLE_PATTERNS,
    _SESSION_COOKIE_NAMES,
    CacheHeadersMiddleware,
)

# ---------------------------------------------------------------- #
# Audited cacheable set                                             #
# ---------------------------------------------------------------- #

# If you intentionally add or remove a cacheable route, update this
# set AND the audit table in the PR description in lockstep. The
# snapshot test below fails until you do.
EXPECTED_CACHEABLE: set[str] = {
    r"^/api/v1/storefront/store-by-subdomain/[^/]+/?$",
    r"^/api/v1/storefront/store-by-domain/[^/]+/?$",
    r"^/api/v1/storefront/store/[0-9a-f-]+/products/?$",
    r"^/api/v1/storefront/store/[0-9a-f-]+/products/cursor/?$",
    r"^/api/v1/storefront/store/[0-9a-f-]+/products/[^/]+/?$",
    r"^/api/v1/storefront/store/[0-9a-f-]+/categories(?:/[^/]+)?/?$",
    r"^/api/v1/storefront/store/[0-9a-f-]+/sitemap-feed/?$",
    r"^/api/v1/storefront/theme/[0-9a-f-]+/?$",
    r"^/api/v1/public/landing-config/?$",
}


# ---------------------------------------------------------------- #
# Minimal app under test                                            #
# ---------------------------------------------------------------- #


async def _ok(_request) -> JSONResponse:
    """Echo a trivial 200 — body is irrelevant to the header check."""
    return JSONResponse({"ok": True})


def _build_app() -> Starlette:
    """A tiny Starlette app with a catch-all GET + the middleware.

    Using a single ``{path:path}`` route lets us exercise the
    middleware on any URL without booting the real router tree.
    """
    app = Starlette(routes=[Route("/{path:path}", _ok)])
    app.add_middleware(CacheHeadersMiddleware)
    return app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(_build_app())


# Sample UUID-looking value for store_id slots in the regex.
_STORE_ID = "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------- #
# 1. Snapshot — allowlist must match the audit table                #
# ---------------------------------------------------------------- #


def test_cacheable_patterns_are_exactly_the_audited_set() -> None:
    actual = {p.pattern for p in _CACHEABLE_PATTERNS}
    assert actual == EXPECTED_CACHEABLE, (
        "Cacheable allowlist drift. If you intentionally added or removed "
        "a cacheable route, update EXPECTED_CACHEABLE in this file AND the "
        "audit table in the PR description. Otherwise revert the change to "
        "src/api/middleware/cache_headers.py."
    )


def test_every_pattern_is_anchored() -> None:
    """A pattern that doesn't anchor both ends can match unintended
    subpaths (``/products`` would match ``/products/internal-admin``).
    """
    for pattern in _CACHEABLE_PATTERNS:
        assert pattern.pattern.startswith("^"), (
            f"{pattern.pattern!r} must start with ^ (anchored)"
        )
        assert pattern.pattern.endswith("$"), (
            f"{pattern.pattern!r} must end with $ (anchored)"
        )


# ---------------------------------------------------------------- #
# 2. Cookie-respect parametrized test                               #
# ---------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "cookie", "expected_directive"),
    [
        # Allowlisted, no cookies → public
        ("/api/v1/storefront/store-by-subdomain/foo", None, "public"),
        ("/api/v1/storefront/store-by-domain/shop.example.com", None, "public"),
        (f"/api/v1/storefront/store/{_STORE_ID}/products", None, "public"),
        (f"/api/v1/storefront/store/{_STORE_ID}/products/cursor", None, "public"),
        (f"/api/v1/storefront/store/{_STORE_ID}/products/demo-tshirt", None, "public"),
        (f"/api/v1/storefront/store/{_STORE_ID}/categories", None, "public"),
        (f"/api/v1/storefront/theme/{_STORE_ID}", None, "public"),
        ("/api/v1/public/landing-config", None, "public"),
        # Allowlisted path WITH cookie → MUST be private (regardless of which session cookie)
        (
            "/api/v1/storefront/store-by-subdomain/foo",
            "access_token=abc",
            "private",
        ),
        (
            "/api/v1/storefront/store-by-subdomain/foo",
            "customer_access_token=xyz",
            "private",
        ),
        (
            "/api/v1/storefront/store-by-subdomain/foo",
            "admin_access_token=q",
            "private",
        ),
        (
            "/api/v1/storefront/store-by-subdomain/foo",
            "csrf_token=q",
            "private",
        ),
        (
            "/api/v1/storefront/store-by-subdomain/foo",
            "numu_cart_session=q",
            "private",
        ),
        (
            f"/api/v1/storefront/store/{_STORE_ID}/products",
            "csrf_token=q",
            "private",
        ),
        # Non-cacheable paths → middleware does not touch Cache-Control
        ("/api/v1/storefront/me/cart", None, None),
        ("/api/v1/storefront/me/profile", "customer_access_token=x", None),
        (f"/api/v1/stores/{_STORE_ID}", None, None),
        (f"/api/v1/stores/{_STORE_ID}/products", None, None),
        ("/api/v1/admin/dashboard", "admin_access_token=q", None),
        ("/api/v1/auth/me", "access_token=q", None),
        # Cacheable regex must NOT match a deeper subpath
        (f"/api/v1/storefront/store/{_STORE_ID}/products/cursor/extra", None, None),
    ],
)
def test_cache_directive_respects_cookies(
    client: TestClient,
    path: str,
    cookie: str | None,
    expected_directive: str | None,
) -> None:
    headers = {"Cookie": cookie} if cookie else {}
    response = client.get(path, headers=headers)
    cc = response.headers.get("cache-control", "")

    if expected_directive is None:
        # Middleware should not have added a public directive.
        assert "public" not in cc, (
            f"{path!r} should not be cacheable but got Cache-Control={cc!r}"
        )
    elif expected_directive == "public":
        assert cc.startswith("public,"), (
            f"{path!r} should be public but got Cache-Control={cc!r}"
        )
        assert "no-store" not in cc
    elif expected_directive == "private":
        assert cc.startswith("private,"), (
            f"{path!r} with cookie {cookie!r} must be private but got "
            f"Cache-Control={cc!r}"
        )
        assert "public" not in cc


# ---------------------------------------------------------------- #
# 3. Authorization header still respected                           #
# ---------------------------------------------------------------- #


def test_authorization_header_forces_private(client: TestClient) -> None:
    response = client.get(
        "/api/v1/storefront/store-by-subdomain/foo",
        headers={"Authorization": "Bearer xyz"},
    )
    cc = response.headers.get("cache-control", "")
    assert cc.startswith("private,")
    assert "public" not in cc


# ---------------------------------------------------------------- #
# 4. Vary header is set on both branches                            #
# ---------------------------------------------------------------- #


def test_vary_includes_cookie_on_public_branch(client: TestClient) -> None:
    response = client.get("/api/v1/storefront/store-by-subdomain/foo")
    vary = response.headers.get("vary", "")
    assert "Cookie" in vary, f"public-branch Vary must include Cookie, got {vary!r}"


def test_vary_includes_cookie_on_private_branch(client: TestClient) -> None:
    response = client.get(
        "/api/v1/storefront/store-by-subdomain/foo",
        headers={"Cookie": "csrf_token=x"},
    )
    vary = response.headers.get("vary", "")
    assert "Cookie" in vary, f"private-branch Vary must include Cookie, got {vary!r}"
    assert "Authorization" in vary


# ---------------------------------------------------------------- #
# 5. Session-cookie completeness                                    #
# ---------------------------------------------------------------- #


def test_session_cookie_names_includes_all_known_session_cookies() -> None:
    """If a new session cookie is introduced anywhere in the codebase,
    add it here too. This guard fails if the middleware can't see it.

    Names sourced from src/api/utils/cookies.py + src/api/v1/routes/auth.py
    (csrf_token) + src/api/v1/routes/storefront/_cart_owner.py
    (numu_cart_session) + src/main.py (Starlette SessionMiddleware).
    """
    must_have = {
        "access_token",
        "refresh_token",
        "customer_access_token",
        "customer_refresh_token",
        "admin_access_token",
        "admin_refresh_token",
        "csrf_token",
        "numu_cart_session",
        "session",
    }
    missing = must_have - set(_SESSION_COOKIE_NAMES)
    assert not missing, (
        f"_SESSION_COOKIE_NAMES is missing known session cookies: {missing}. "
        "Add the cookie name to the tuple so the middleware can detect it."
    )


# ---------------------------------------------------------------- #
# 6. Leak test — the single most important assertion in this file   #
# ---------------------------------------------------------------- #


_LEAK_TEST_PATHS: list[str] = [
    "/api/v1/storefront/store-by-subdomain/load-store-1",
    "/api/v1/storefront/store-by-domain/shop.example.com",
    f"/api/v1/storefront/store/{_STORE_ID}/products",
    f"/api/v1/storefront/store/{_STORE_ID}/products/cursor",
    f"/api/v1/storefront/store/{_STORE_ID}/products/demo-tshirt",
    f"/api/v1/storefront/store/{_STORE_ID}/categories",
    f"/api/v1/storefront/store/{_STORE_ID}/categories/cat-slug",
    f"/api/v1/storefront/theme/{_STORE_ID}",
    "/api/v1/public/landing-config",
]


@pytest.mark.parametrize("path", _LEAK_TEST_PATHS)
@pytest.mark.parametrize("cookie_name", list(_SESSION_COOKIE_NAMES))
def test_no_cacheable_route_leaks_with_session_cookie(
    client: TestClient,
    path: str,
    cookie_name: str,
) -> None:
    """For every cacheable allowlist entry, with every known session
    cookie set, the response MUST be ``private, no-store``. If this
    test ever fails, something is silently caching authenticated data
    and Cloudflare would serve one user's response to another.

    This test is parametrized over the full cross-product so a new
    cookie name OR a new cacheable route is covered automatically.
    """
    response = client.get(path, headers={"Cookie": f"{cookie_name}=fake"})
    cc = response.headers.get("cache-control", "")
    assert "private" in cc, (
        f"{path} returned Cache-Control={cc!r} with {cookie_name} cookie — "
        "must be 'private, no-store' to prevent cross-user cache leaks"
    )
    assert "public" not in cc, (
        f"{path} returned Cache-Control={cc!r} with {cookie_name} cookie — "
        "'public' must NEVER appear when a session cookie is present"
    )
    assert "no-store" in cc, (
        f"{path} returned Cache-Control={cc!r} with {cookie_name} cookie — "
        "'no-store' is required so downstream caches don't store the response"
    )
