"""The line between what a third party may call and what it may not.

Four hardening changes meet here, and each one is a hole if it silently
regresses: the internal schema must not be published, the public one must not
carry anything a token cannot call, a merchant-supplied webhook URL must not
reach our own network, and a new token must name its scopes and expire.

Pure logic; no Postgres, no network (the addresses used are literals, so the
SSRF check resolves them without a DNS lookup).
"""

import pytest
from pydantic import ValidationError

from src.api.v1.routes.public.openapi import build_public_schema
from src.api.v1.routes.stores.access_tokens import CreateAccessTokenRequest
from src.core.url_guard import UnsafeUrlError, assert_public_http_url
from src.main import _should_expose_docs

PRIVATE_TARGETS = (
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://127.0.0.1:8000/hook",
    "http://[::1]/hook",
    "http://10.0.0.5/hook",
    "https://192.168.1.10/hook",
    "http://[::ffff:169.254.169.254]/hook",
)


def test_internal_schema_is_not_published_without_credentials(monkeypatch):
    """Production runs with ENVIRONMENT=staging, which used to expose it."""
    import src.main as main

    monkeypatch.setattr(main.settings, "debug", False)
    monkeypatch.setattr(main.settings, "environment", "staging")
    monkeypatch.setattr(main.settings, "docs_username", "")
    monkeypatch.setattr(main.settings, "docs_password", "")
    assert _should_expose_docs() is False

    monkeypatch.setattr(main.settings, "environment", "production")
    assert _should_expose_docs() is False


def test_internal_schema_needs_both_halves_of_the_credential(monkeypatch):
    """A half-configured pair is a password-less door, not a protected one."""
    import src.main as main

    monkeypatch.setattr(main.settings, "debug", False)
    monkeypatch.setattr(main.settings, "environment", "staging")
    monkeypatch.setattr(main.settings, "docs_username", "admin")
    monkeypatch.setattr(main.settings, "docs_password", "")
    assert _should_expose_docs() is False

    monkeypatch.setattr(main.settings, "docs_password", "secret")
    assert _should_expose_docs() is True


@pytest.mark.parametrize("url", PRIVATE_TARGETS)
def test_webhook_targets_inside_our_network_are_refused(url):
    """The server POSTs to this URL, so an internal one is an SSRF proxy."""
    with pytest.raises(UnsafeUrlError):
        assert_public_http_url(url, allow_private=False)


def test_link_local_is_refused_even_in_development():
    """Cloud metadata is never a webhook target, whatever the environment."""
    with pytest.raises(UnsafeUrlError):
        assert_public_http_url(
            "http://169.254.169.254/latest/meta-data/", allow_private=True
        )


def test_a_new_token_must_name_its_scopes():
    with pytest.raises(ValidationError):
        CreateAccessTokenRequest(name="ci")
    with pytest.raises(ValidationError):
        CreateAccessTokenRequest(name="ci", scopes=[])


def test_a_new_token_expires_by_default():
    assert CreateAccessTokenRequest(name="ci", scopes=["orders:read"]).expires_in_days


# ── The published contract ──────────────────────────────────────────────

_FULL = {
    "openapi": "3.1.0",
    "info": {"title": "NUMU API", "version": "0.1.0"},
    "paths": {
        "/api/v1/stores/{store_id}/orders/": {
            "get": {"responses": {"200": {"$ref": "#/components/schemas/OrderList"}}},
            "post": {"responses": {"201": {}}},
        },
        "/api/v1/admin/tenants/": {"get": {"responses": {"200": {}}}},
        "/api/v1/stores/{store_id}/access-tokens": {"post": {"responses": {"201": {}}}},
        "/api/v1/billing/invoices": {"get": {"responses": {"200": {}}}},
        "/api/v1/auth/api-key/me": {"get": {"responses": {"200": {}}}},
    },
    "components": {
        "schemas": {
            "OrderList": {"items": {"$ref": "#/components/schemas/Order"}},
            "Order": {"type": "object"},
            "AdminTenant": {"type": "object"},
        }
    },
}


def test_public_schema_carries_only_what_a_token_can_call():
    public = build_public_schema(_FULL)

    assert set(public["paths"]) == {
        "/api/v1/stores/{store_id}/orders/",
        "/api/v1/auth/api-key/me",
    }
    # Admin, billing, and token management (privilege escalation) stay out.
    assert not [p for p in public["paths"] if "/admin/" in p or "billing" in p]
    assert "/api/v1/stores/{store_id}/access-tokens" not in public["paths"]


def test_the_contract_says_where_the_api_lives():
    """Without this an imported client points at nothing and every request
    has to be re-pointed by hand."""
    servers = build_public_schema(_FULL)["servers"]

    assert servers[0]["url"] == "https://numueg.app/api/v1"


def test_every_published_operation_states_its_scope_and_auth():
    orders = build_public_schema(_FULL)["paths"]["/api/v1/stores/{store_id}/orders/"]

    assert orders["get"]["x-numu-scope"] == "orders:read"
    assert orders["post"]["x-numu-scope"] == "orders:write"
    assert orders["get"]["security"] == [{"bearerAuth": []}]


def test_internal_model_shapes_are_pruned_from_the_contract():
    """Components describe the admin surface field by field otherwise."""
    schemas = build_public_schema(_FULL)["components"]["schemas"]

    assert set(schemas) == {"OrderList", "Order"}  # transitively reachable only
