"""Theme suspension — the ADR-6 kill switch.

`MarketplaceThemeStatus.SUSPENDED` shipped as an enum value with no endpoint
that could set it and no code that read it: a lever connected to nothing. An
admin could "suspend" a malicious theme and it would keep serving every store
that had it active.

These pin the two halves that make it real — the lever, and the thing it pulls
— plus the property that makes it usable: suspension must stop the code
WITHOUT taking merchants offline, or nobody would ever dare pull it.
"""

from types import SimpleNamespace

import pytest

from src.application.services.theme_service import ThemeService
from src.core.entities.marketplace_theme import MarketplaceThemeStatus


def _svc(listing=None, raises=False):
    class Repo:
        async def get_theme_by_slug(self, slug):
            if raises:
                raise RuntimeError("db blip")
            return listing

    return ThemeService(
        theme_repo=None,
        version_repo=None,
        store_theme_repo=None,
        marketplace_repo=Repo(),
    )


def _active(theme_type="external", slug="evil-theme"):
    return SimpleNamespace(
        theme_type=SimpleNamespace(value=theme_type), theme_slug=slug
    )


class TestSuspensionDetection:
    @pytest.mark.asyncio
    async def test_suspended_external_theme_is_detected(self):
        listing = SimpleNamespace(status=MarketplaceThemeStatus.SUSPENDED)
        assert await _svc(listing)._is_theme_suspended(_active()) is True

    @pytest.mark.asyncio
    async def test_published_theme_is_not_suspended(self):
        listing = SimpleNamespace(status=MarketplaceThemeStatus.PUBLISHED)
        assert await _svc(listing)._is_theme_suspended(_active()) is False

    @pytest.mark.asyncio
    async def test_builtin_theme_short_circuits(self):
        # A built-in theme has no marketplace listing to suspend. It must not
        # even reach the lookup.
        class Boom:
            async def get_theme_by_slug(self, slug):
                raise AssertionError("must not query for a built-in theme")

        svc = ThemeService(
            theme_repo=None,
            version_repo=None,
            store_theme_repo=None,
            marketplace_repo=Boom(),
        )
        assert await svc._is_theme_suspended(_active(theme_type="internal")) is False

    @pytest.mark.asyncio
    async def test_missing_listing_is_not_suspended(self):
        assert await _svc(None)._is_theme_suspended(_active()) is False


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_lookup_error_does_not_suspend(self):
        # A transient DB blip must NOT drop every storefront on the platform
        # to the built-in renderer. Missing one suspension for one request is
        # far cheaper than a platform-wide false positive.
        assert await _svc(raises=True)._is_theme_suspended(_active()) is False

    @pytest.mark.asyncio
    async def test_no_marketplace_repo_does_not_suspend(self):
        # Construction sites that predate this feature pass no repo.
        svc = ThemeService(theme_repo=None, version_repo=None, store_theme_repo=None)
        assert await svc._is_theme_suspended(_active()) is False


class TestKillSwitch:
    """The lever: MarketplaceService.set_theme_suspension."""

    def _service(self, theme, installs=()):
        from src.application.services.marketplace_service import MarketplaceService

        updates = {}

        class Repo:
            async def get_theme_by_id(self, tid):
                return theme

            async def update_theme(self, tid, patch):
                updates.update(patch)
                return theme

            async def list_installations_for_theme(self, tid):
                return list(installs)

        svc = MarketplaceService(marketplace_repo=Repo())
        return svc, updates

    @pytest.mark.asyncio
    async def test_suspend_sets_status_and_busts_each_store(self):
        theme = SimpleNamespace(
            slug="evil-theme", status=MarketplaceThemeStatus.PUBLISHED
        )
        installs = [SimpleNamespace(store_id="s1"), SimpleNamespace(store_id="s2")]
        svc, updates = self._service(theme, installs)

        busted = []

        class Cache:
            async def invalidate_theme(self, store_id):
                busted.append(store_id)

        result = await svc.set_theme_suspension(
            theme_id="t1", suspended=True, storefront_cache=Cache()
        )

        assert updates["status"] == MarketplaceThemeStatus.SUSPENDED.value
        # A kill switch with a cache-shaped delay isn't one.
        assert busted == ["s1", "s2"]
        assert result["affected_store_count"] == 2

    @pytest.mark.asyncio
    async def test_cache_failure_does_not_block_suspension(self):
        # Enforcement is delayed to the next cache miss, not defeated — and
        # failing the suspension over a cache error would be far worse.
        theme = SimpleNamespace(slug="x", status=MarketplaceThemeStatus.PUBLISHED)
        svc, updates = self._service(theme, [SimpleNamespace(store_id="s1")])

        class BadCache:
            async def invalidate_theme(self, store_id):
                raise RuntimeError("redis down")

        result = await svc.set_theme_suspension(
            theme_id="t1", suspended=True, storefront_cache=BadCache()
        )
        assert updates["status"] == MarketplaceThemeStatus.SUSPENDED.value
        assert result["suspended"] is True

    @pytest.mark.asyncio
    async def test_reinstate_requires_a_suspended_theme(self):
        theme = SimpleNamespace(slug="x", status=MarketplaceThemeStatus.PUBLISHED)
        svc, _ = self._service(theme)
        with pytest.raises(ValueError, match="not suspended"):
            await svc.set_theme_suspension(theme_id="t1", suspended=False)

    @pytest.mark.asyncio
    async def test_reinstate_restores_published(self):
        theme = SimpleNamespace(slug="x", status=MarketplaceThemeStatus.SUSPENDED)
        svc, updates = self._service(theme)
        await svc.set_theme_suspension(theme_id="t1", suspended=False)
        assert updates["status"] == MarketplaceThemeStatus.PUBLISHED.value

    @pytest.mark.asyncio
    async def test_unknown_theme_raises(self):
        svc, _ = self._service(None)
        with pytest.raises(ValueError, match="not found"):
            await svc.set_theme_suspension(theme_id="t1", suspended=True)
