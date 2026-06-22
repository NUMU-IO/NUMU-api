"""Next.js revalidation cache-tag agreement (Phase 1.5).

The publish-time cache miss was a tag mismatch: the backend posted
``theme:{id}`` (colon) while the storefront tagged its theme fetch
``theme-${storeId}`` (hyphen), so ``revalidateTag`` was a silent no-op and
Publish never refreshed the live store until the ISR window lapsed.

These tests pin the exact tag formats the storefront depends on
(``numu-storefront/src/lib/api-client.ts``) so the mismatch can't regress.
They never hit the network — the high-level helpers are exercised with a
stubbed ``revalidate_store`` that just records the tags it was asked to bust.
"""

import pytest

from src.infrastructure.external_services import nextjs_revalidation as rv


class TestCacheTagFormat:
    def test_theme_tag_uses_hyphen_not_colon(self):
        assert rv.theme_cache_tag("abc-123") == "theme-abc-123"
        assert ":" not in rv.theme_cache_tag("abc-123")

    def test_store_tag_format(self):
        assert rv.store_cache_tag("sawsaw") == "store-sawsaw"

    def test_store_cache_tags_subdomain_only(self):
        assert rv.store_cache_tags("sawsaw") == ["store-sawsaw"]

    def test_store_cache_tags_with_custom_domain(self):
        assert rv.store_cache_tags("sawsaw", "shop.mybrand.com") == [
            "store-sawsaw",
            "store-shop.mybrand.com",
        ]


class TestRevalidateHelperTags:
    @pytest.fixture
    def captured(self, monkeypatch):
        calls: list[dict] = []

        async def fake_revalidate_store(subdomain, paths=None, tags=None, scope=None):
            calls.append({
                "subdomain": subdomain,
                "paths": paths,
                "tags": tags,
                "scope": scope,
            })
            return True

        monkeypatch.setattr(rv, "revalidate_store", fake_revalidate_store)
        return calls

    @pytest.mark.asyncio
    async def test_theme_activate_busts_theme_and_store(self, captured):
        await rv.revalidate_on_theme_activate("mystore", "store-id-1")
        assert captured[0]["tags"] == ["theme-store-id-1", "store-mystore"]
        assert captured[0]["scope"] == "layout"

    @pytest.mark.asyncio
    async def test_theme_activate_busts_custom_domain_tag(self, captured):
        # Custom-domain stores must also bust their `store-{host}` payload on a
        # theme activate, else they keep rendering the old theme until the ISR
        # window lapses.
        await rv.revalidate_on_theme_activate(
            "mystore", "store-id-1", custom_domain="shop.mybrand.com"
        )
        assert captured[0]["tags"] == [
            "theme-store-id-1",
            "store-mystore",
            "store-shop.mybrand.com",
        ]

    @pytest.mark.asyncio
    async def test_publish_busts_theme_and_store_tags(self, captured):
        # Publish must bust the base store payload tag too — store name, logo,
        # SEO, social and theme_settings ride the 300s `store-` cache entry.
        await rv.revalidate_on_customization_publish("mystore", "s1")
        assert captured[0]["tags"] == ["theme-s1", "store-mystore"]
        assert captured[0]["scope"] == "layout"

    @pytest.mark.asyncio
    async def test_publish_busts_custom_domain_tag(self, captured):
        await rv.revalidate_on_customization_publish(
            "mystore", "s1", custom_domain="shop.mybrand.com"
        )
        assert captured[0]["tags"] == [
            "theme-s1",
            "store-mystore",
            "store-shop.mybrand.com",
        ]

    @pytest.mark.asyncio
    async def test_menu_change_busts_menus_and_theme(self, captured):
        await rv.revalidate_on_menu_change("mystore", "s1")
        assert captured[0]["tags"] == ["menus-s1", "theme-s1"]

    @pytest.mark.asyncio
    async def test_page_change_busts_pages_and_theme(self, captured):
        await rv.revalidate_on_page_change("mystore", "s1", "about-us")
        assert captured[0]["tags"] == ["pages-s1", "theme-s1"]
        assert captured[0]["paths"] == ["/pages/about-us"]


class _FakeResp:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        return self._body


class _FakeClient:
    """Records the last POST and returns a canned response."""

    last: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.last = {"url": url, "headers": headers, "json": json}
        return _FakeResp(
            200,
            {
                "success": True,
                "tagsReceived": json.get("tags", []),
                "tagsRevalidated": json.get("tags", []),
                "durationMs": 3,
            },
        )


class TestTracedRevalidation:
    """The publish path uses the *traced* helpers so it can report a structured
    freshness outcome back to the merchant UI."""

    @pytest.fixture
    def configured(self, monkeypatch):
        monkeypatch.setattr(rv, "REVALIDATION_SECRET", "test-secret")
        monkeypatch.setattr(rv, "STOREFRONT_BASE_URL", "http://127.0.0.1:3100")
        monkeypatch.setattr(rv.httpx, "AsyncClient", _FakeClient)
        _FakeClient.last = {}

    @pytest.mark.asyncio
    async def test_traced_publish_posts_full_tag_set_and_reports_success(
        self, configured
    ):
        summary = await rv.revalidate_on_customization_publish_traced(
            "mystore", "s1", custom_domain="shop.mybrand.com"
        )
        # The POST carried the full tag set.
        assert _FakeClient.last["json"]["tags"] == [
            "theme-s1",
            "store-mystore",
            "store-shop.mybrand.com",
        ]
        # And the summary reflects the storefront's structured echo.
        assert summary.requested is True
        assert summary.succeeded is True
        assert summary.status_code == 200
        assert summary.tags_revalidated == [
            "theme-s1",
            "store-mystore",
            "store-shop.mybrand.com",
        ]
        assert summary.error is None

    @pytest.mark.asyncio
    async def test_traced_reports_not_requested_when_secret_missing(self, monkeypatch):
        monkeypatch.setattr(rv, "REVALIDATION_SECRET", "")
        summary = await rv.revalidate_on_customization_publish_traced("mystore", "s1")
        assert summary.requested is False
        assert summary.succeeded is False
        assert summary.error == "revalidation_secret_not_configured"

    @pytest.mark.asyncio
    async def test_traced_reports_http_failure(self, monkeypatch):
        monkeypatch.setattr(rv, "REVALIDATION_SECRET", "test-secret")
        monkeypatch.setattr(rv, "STOREFRONT_BASE_URL", "http://127.0.0.1:3100")

        class _FailClient(_FakeClient):
            async def post(self, url, headers=None, json=None):
                return _FakeResp(503, {}, text="unavailable")

        monkeypatch.setattr(rv.httpx, "AsyncClient", _FailClient)
        summary = await rv.revalidate_on_customization_publish_traced("mystore", "s1")
        assert summary.requested is True
        assert summary.succeeded is False
        assert summary.status_code == 503
        assert "http_503" in (summary.error or "")
