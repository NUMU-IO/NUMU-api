"""Partner App tokens: the scope rule, what an app may never be granted, and
the signed query string (plan 03 §§ 5, 6.3)."""

import hashlib
import hmac

import pytest

from src.application.services.app_manifest import APP_SCOPES
from src.application.services.app_tokens import (
    required_app_scope,
    sign_params,
    signed_params,
)
from src.application.services.personal_access_token_service import (
    required_scope_for,
    scope_allows,
)

STORE = "/api/v1/stores/0b1e2c3d-0000-0000-0000-000000000000"


@pytest.mark.parametrize(
    ("path", "method", "expected"),
    [
        (f"{STORE}/orders/", "GET", "orders:read"),
        (f"{STORE}/orders/", "POST", "orders:write"),
        (f"{STORE}/threads/", "GET", "messages:read"),
        (f"{STORE}/threads/x/messages/send", "POST", "messages:write"),
        (f"{STORE}/channels/", "GET", "messages:read"),
        (f"{STORE}/whatsapp/templates", "GET", "messages:read"),
        (f"{STORE}/coupons/", "GET", "marketing:read"),
        (f"{STORE}/access-tokens/", "GET", None),  # never, like a PAT
        # plan 03 § 5 "never grantable": the whole settings domain, which holds
        # payment-gateway credentials, payment proofs, billing, publishing and
        # the store's other app installations; risk is read-only.
        (f"{STORE}/settings/payment/kashier/credentials", "PUT", None),
        (f"{STORE}/settings/payment/kashier/credentials", "GET", None),
        (f"{STORE}/settings/customization/publish", "POST", None),
        (f"{STORE}/payment-proofs/", "GET", None),
        (f"{STORE}/billing/", "GET", None),
        (f"{STORE}/apps/", "GET", None),
        (f"{STORE}/apps/some-app/install", "POST", None),
        (f"{STORE}/risk/", "GET", "risk:read"),
        (f"{STORE}/risk/", "POST", None),
        (f"{STORE}/themes/", "POST", None),
        ("/api/v1/auth/api-key/me", "GET", "__identity__"),
        ("/api/v1/admin/partners", "GET", None),
    ],
)
def test_required_app_scope(path, method, expected):
    assert required_app_scope(path, method) == expected


def test_a_marketing_only_app_cannot_read_customer_conversations():
    """The plan's named test: a discount app must not read the store's DMs."""
    required = required_app_scope(f"{STORE}/threads/", "GET")
    assert not scope_allows(["marketing:read", "marketing:write"], required)
    assert scope_allows(["messages:read"], required)


def test_a_pat_still_reaches_conversations_with_marketing():
    """PAT behaviour is unchanged: no existing integration breaks."""
    required = required_scope_for(f"{STORE}/threads/", "GET")
    assert required == "marketing:read"
    assert scope_allows(["marketing:read"], required)


def test_what_an_app_may_never_be_granted():
    never = {"*", "themes:write", "risk:write", "settings:read", "settings:write"}
    assert not never & APP_SCOPES
    assert {"messages:read", "messages:write", "themes:read", "risk:read"} <= APP_SCOPES


def test_a_stored_grant_cannot_reopen_a_never_grantable_scope():
    """Even a token whose stored scopes carry settings:write is refused:
    the request needs a scope apps may not hold, so none satisfies it."""
    required = required_app_scope(
        f"{STORE}/settings/payment/kashier/credentials", "PUT"
    )
    assert required is None


def test_signed_query_matches_the_documented_algorithm():
    """hex HMAC-SHA256 over the other params, URL-decoded, sorted, k=v&k=v."""
    params = {"store_id": "abc", "locale": "ar", "timestamp": "1700000000"}
    expected = hmac.new(
        b"secret",
        b"locale=ar&store_id=abc&timestamp=1700000000",
        hashlib.sha256,
    ).hexdigest()
    assert sign_params(params, "secret") == expected
    assert sign_params({**params, "hmac": "ignored"}, "secret") == expected


def test_signed_params_stamp_and_verify():
    signed = signed_params({"store_id": "abc", "state": "x y"}, "s3cret")
    assert signed["hmac"] == sign_params(signed, "s3cret")
    assert signed["hmac"] != sign_params(signed, "wrong")
