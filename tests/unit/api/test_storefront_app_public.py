"""Tests for the public projection of an app install.

`/api/v1/storefront/**` is in `PUBLIC_PATH_PREFIXES`, so tenancy middleware is
skipped, the routers carry no dependencies, and `store_id` comes straight off
the URL and is never checked against the requesting host. Host-to-store binding
lives only in the Next.js proxy, which is not on the path when `api.numueg.app`
is called directly.

So anything these routes return is world-readable by anyone who can guess a
store UUID — and `app_installations.settings` is the field the entity docstring
describes as holding "API tokens".

Today this is latent: zero app rows means both endpoints return `[]` or 404.
It becomes a real disclosure with the FIRST seeded row, which is also step one
of shipping the swatch app. These tests pin the default-deny behaviour so the
projection cannot quietly become permissive later.
"""

from __future__ import annotations

import pytest

from src.api.v1.routes.storefront.app_public import public_manifest, public_settings


class TestPublicSettingsIsDefaultDeny:
    @pytest.mark.parametrize(
        "manifest",
        [
            None,
            {},
            {"public_settings": None},
            {"public_settings": "swatch_shape"},  # a string, not a list
            {"public_settings": []},
            {"public_settings": [1, 2, 3]},  # no usable string keys
        ],
    )
    def test_nothing_is_public_without_a_usable_allowlist(self, manifest):
        secrets = {"api_token": "sk-live-abc", "swatch_shape": "circle"}
        assert public_settings(manifest, secrets) == {}

    def test_only_declared_keys_are_exposed(self):
        manifest = {"public_settings": ["swatch_shape", "out_of_stock"]}
        settings = {
            "swatch_shape": "circle",
            "out_of_stock": "strike",
            "api_token": "sk-live-abc",
            "webhook_secret": "whsec_xyz",
        }
        assert public_settings(manifest, settings) == {
            "swatch_shape": "circle",
            "out_of_stock": "strike",
        }

    def test_a_declared_key_the_merchant_never_set_is_simply_absent(self):
        manifest = {"public_settings": ["swatch_shape", "never_configured"]}
        assert public_settings(manifest, {"swatch_shape": "pill"}) == {
            "swatch_shape": "pill"
        }

    def test_empty_settings_are_tolerated(self):
        assert public_settings({"public_settings": ["a"]}, None) == {}


class TestPublicManifest:
    def test_strips_everything_not_on_the_allowlist(self):
        manifest = {
            "settings_schema": [{"id": "swatch_shape"}],
            "blocks": [{"type": "swatch"}],
            "version": "1.0.0",
            # None of the following may reach a shopper's browser.
            "oauth_client_secret": "shh",
            "webhook_url": "https://vendor.example/hook",
            "scopes": ["products:write"],
            "internal_notes": "…",
        }
        out = public_manifest(manifest)
        assert set(out) == {"settings_schema", "blocks", "version"}
        assert "oauth_client_secret" not in out
        assert "webhook_url" not in out
        assert "scopes" not in out

    @pytest.mark.parametrize("manifest", [None, "not-a-dict", 42, []])
    def test_a_malformed_manifest_projects_to_empty(self, manifest):
        assert public_manifest(manifest) == {}

    def test_a_new_manifest_key_is_invisible_until_allowlisted(self):
        # An allowlist rather than a denylist: whoever adds a manifest key does
        # not have to remember to hide it.
        assert public_manifest({"some_future_key": "value"}) == {}


def test_the_storefront_route_uses_the_projection():
    """Source-level invariant: the route must not echo raw fields again.

    The leak was `manifest=manifest` / `settings=install.settings or {}` written
    inline. A future edit that reinstates either bypasses every test above, so
    this pins the call site rather than only the helper.
    """
    import inspect

    from src.api.v1.routes.storefront import apps

    source = inspect.getsource(apps)
    assert "settings=public_settings(" in source
    assert "manifest=public_manifest(" in source
    assert "settings=install.settings or {}" not in source
