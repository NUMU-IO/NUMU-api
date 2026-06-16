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
