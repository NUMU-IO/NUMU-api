"""Wave 3 app-platform behaviour: merge semantics, status honesty, flag writes.

Each test here pins a defect that was live in the code, not a hypothetical:

  * `PUT /{slug}/settings` replaced the whole blob, so two overlapping saves
    silently lost the first one's keys — no ETag, no version history, nothing
    to notice it with. It also busted no cache at all, while app settings ride
    the store payload that both the API and the storefront cache for 60s.
  * `list_installations` ignored `AppModel.status` while the storefront filters
    on it, so a suspended app kept reading as installed-and-working in the hub
    while shoppers saw nothing.
  * Nothing anywhere could WRITE `tenant.feature_flags`, so every dark-launch
    flip was hand-written SQL against production — and a plain assignment would
    drop `golive_exempt`, which both live tenants carry, and start refusing
    real orders.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.v1.routes.stores.apps import _installation
from src.core.entities.app import AppStatus


def app(status=AppStatus.PUBLISHED, **kw):
    return SimpleNamespace(
        slug=kw.get("slug", "variant-swatches"),
        name=kw.get("name", "Variant Swatches"),
        description=None,
        icon_url=None,
        version="1.0.0",
        status=status,
        manifest=kw.get("manifest", {}),
        developer_id=kw.get("developer_id"),
    )


def install(is_enabled=True, settings=None, status="active"):
    return SimpleNamespace(
        is_enabled=is_enabled, settings=settings or {}, status=status, granted_scopes=[]
    )


class TestInstallationStatusHonesty:
    def test_a_published_enabled_install_is_live(self):
        out = _installation(app(), install())
        assert out.is_live is True
        assert out.app_status == "published"

    def test_a_suspended_app_is_not_live_even_while_enabled(self):
        # The desync: the storefront filters on status, the hub did not.
        out = _installation(app(status=AppStatus.SUSPENDED), install(is_enabled=True))
        assert out.is_live is False
        assert out.app_status == "suspended"

    def test_a_suspended_app_is_still_returned_not_hidden(self):
        # Dropping it from the list reads to a merchant as "my settings are
        # gone", which is a worse lie than the one being fixed.
        out = _installation(app(status=AppStatus.SUSPENDED), install(settings={"a": 1}))
        assert out.slug == "variant-swatches"
        assert out.settings == {"a": 1}

    def test_a_disabled_install_is_not_live(self):
        assert _installation(app(), install(is_enabled=False)).is_live is False

    def test_a_mid_consent_install_is_not_live(self):
        # The storefront ignores pending_auth installs, so the hub must too.
        assert _installation(app(), install(status="pending_auth")).is_live is False

    def test_a_plain_string_status_is_tolerated(self):
        # The column is a native Postgres ENUM; depending on how the row was
        # loaded the attribute may already be a str.
        out = _installation(app(status="published"), install())
        assert out.app_status == "published"
        assert out.is_live is True


class TestSettingsMerge:
    """The merge itself, expressed exactly as the route applies it."""

    @staticmethod
    def apply(existing, incoming, replace=False):
        return incoming if replace else {**(existing or {}), **incoming}

    def test_a_partial_save_keeps_keys_it_did_not_send(self):
        existing = {"swatch_shape": "circle", "out_of_stock": "strike"}
        assert self.apply(existing, {"swatch_shape": "square"}) == {
            "swatch_shape": "square",
            "out_of_stock": "strike",
        }

    def test_two_overlapping_saves_do_not_lose_the_first(self):
        # An onboarding step and a settings form saving different keys.
        after_first = self.apply({}, {"color_option_names": "Color,اللون"})
        after_second = self.apply(after_first, {"swatch_shape": "pill"})
        assert after_second == {
            "color_option_names": "Color,اللون",
            "swatch_shape": "pill",
        }

    def test_replace_is_still_available_because_it_is_the_only_way_to_remove(self):
        assert self.apply({"a": 1, "b": 2}, {"a": 1}, replace=True) == {"a": 1}

    def test_an_empty_merge_is_a_no_op(self):
        assert self.apply({"a": 1}, {}) == {"a": 1}


class TestFeatureFlagMerge:
    """`golive_exempt` surviving a flip is the whole point of the endpoint."""

    @staticmethod
    def apply(before, flags):
        return {**(before or {}), **flags}

    def test_golive_exempt_survives_an_unrelated_flip(self):
        before = {"golive_exempt": True}
        after = self.apply(before, {"ff_apps_v1": True})
        assert after["golive_exempt"] is True
        assert after["ff_apps_v1"] is True

    def test_a_flag_can_be_turned_off_without_clearing_the_others(self):
        before = {"golive_exempt": True, "ff_apps_v1": True}
        after = self.apply(before, {"ff_apps_v1": False})
        assert after == {"golive_exempt": True, "ff_apps_v1": False}

    def test_an_empty_tenant_map_is_tolerated(self):
        assert self.apply(None, {"ff_apps_v1": True}) == {"ff_apps_v1": True}


def test_the_settings_route_merges_and_busts_both_caches():
    """Source-level invariant on the route itself.

    The helper tests above pin the semantics; this pins that the ROUTE actually
    uses them, since the original defect was the route doing a bare assignment
    and calling no cache at all.
    """
    import inspect

    from src.api.v1.routes.stores import apps

    src = inspect.getsource(apps.update_settings)
    assert "body.replace" in src
    assert "_revalidate_app_settings(store)" in src
    assert "install.settings = body.settings or {}" not in src

    helper = inspect.getsource(apps._revalidate_app_settings)
    assert "invalidate_store" in helper  # the API's own Redis copy
    # The singleton accessor, never `StorefrontCache()`: the constructor takes
    # a required redis client, so constructing one here raised a TypeError that
    # the best-effort except swallowed, and the bust silently never ran.
    assert "await get_storefront_cache().invalidate_store" in helper
    assert "await StorefrontCache(" not in helper
    assert "revalidate_store" in helper  # the storefront's fetch cache
    assert "store_cache_tags" in helper


def test_the_feature_flag_route_merges_rather_than_assigns():
    import inspect

    from src.api.v1.routes import tenants

    src = inspect.getsource(tenants.patch_tenant_feature_flags)
    assert "{**before, **body.flags}" in src
    # JSONB is mutable-in-place; without this the commit can write nothing.
    assert "flag_modified" in src


@pytest.mark.parametrize(
    "slug,expected",
    [("variant-swatches", True)],
)
def test_the_seed_script_declares_a_public_settings_allowlist(slug, expected):
    """A seeded app with no allowlist would expose nothing — or, if the
    projection ever regressed to a denylist, everything. Pin the contract."""
    # Loaded by PATH, not by package name. `tests/unit/scripts` and
    # `tests/scripts` both exist, and pytest puts a test file's directory on
    # sys.path, so `import scripts.seed_apps` resolved to whichever `scripts`
    # package came first — the import passed alone and raised
    # ModuleNotFoundError the moment a test under tests/unit ran beside it.
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "seed_apps.py"
    spec = importlib.util.spec_from_file_location("numu_seed_apps", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    APPS = module.APPS

    entry = next(a for a in APPS if a["slug"] == slug)
    allowlist = entry["manifest"]["public_settings"]
    assert isinstance(allowlist, list) and allowlist
    declared = {s["id"] for s in entry["manifest"]["settings_schema"]}
    # Every publishable key must be a real setting, or the allowlist is lying.
    assert set(allowlist) <= declared
