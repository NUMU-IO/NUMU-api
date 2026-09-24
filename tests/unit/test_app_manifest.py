"""numu.app.json v1: one test per rule (plan 03 § 4), plus the projection
into the listing shape the hub and storefront already read."""

import copy

import pytest
from pydantic import ValidationError

from src.api.v1.routes.stores.apps import _listing
from src.application.services.app_manifest import (
    ManifestV1,
    app_subscriptions,
    change_type,
    semver_key,
    to_listing_manifest,
    validate_settings,
)

GOOD = {
    "manifest_version": 1,
    "slug": "bosta-sync",
    "version": "1.2.0",
    "type": ["connected"],
    "name": {"ar": "مزامنة بوسطة", "en": "Bosta Sync"},
    "tagline": {
        "ar": "شحناتك من بوسطة أوتوماتيك",
        "en": "Your Bosta shipments, automatic",
    },
    "description": {"ar": "وصف بالعربي", "en": "An English description"},
    "icon": "https://cdn.example.com/icon.png",
    "screenshots": [
        {
            "src": "https://cdn.example.com/1.png",
            "caption": {"ar": "لقطة", "en": "Shot"},
        }
    ],
    "category": "shipping",
    "developer": {
        "support_email": "help@example.com",
        "privacy_policy_url": "https://example.com/privacy",
    },
    "app_url": "https://app.example.com/numu",
    "oauth": {
        "redirect_urls": ["https://app.example.com/numu/callback"],
        "scopes": ["orders:read", "orders:write"],
    },
    "webhooks": [
        {"event": "order.paid", "url": "https://app.example.com/hooks"},
        {"event": "app.uninstalled", "url": "https://app.example.com/hooks"},
    ],
    "settings_schema": [
        {
            "type": "header",
            "locales": {"ar": {"content": "عام"}, "en": {"content": "General"}},
        },
        {
            "id": "auto_sync",
            "type": "checkbox",
            "default": True,
            "locales": {
                "ar": {"label": "مزامنة تلقائية"},
                "en": {"label": "Auto sync"},
            },
        },
        {
            "id": "mode",
            "type": "select",
            "options": [{"value": "fast"}, {"value": "cheap"}],
            "locales": {"ar": {"label": "الوضع"}, "en": {"label": "Mode"}},
        },
    ],
    "pricing": {"model": "free"},
}


def bad(**changes):
    m = copy.deepcopy(GOOD)
    for path, value in changes.items():
        node = m
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        if value is DELETE:
            del node[keys[-1]]
        else:
            node[keys[-1]] = value
    return m


DELETE = object()


def test_the_example_manifest_is_valid():
    ManifestV1.model_validate(GOOD)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"slug": "Bosta"}, "slug"),
        ({"version": "1.2"}, "semver"),
        ({"type": ["extension"]}, "type"),
        ({"name": {"ar": "Bosta Sync", "en": "Bosta Sync"}}, "Arabic"),
        ({"tagline": {"ar": "ا" * 81, "en": "x"}}, "80 characters"),
        ({"icon": "http://cdn.example.com/icon.png"}, "https"),
        ({"app_url": "https://127.0.0.1/numu"}, "public host"),
        ({"app_url": "https://shop.localhost/numu"}, "public host"),
        ({"oauth__scopes": ["orders:read", "*"]}, "unknown scopes"),
        ({"oauth__scopes": ["orders:delete"]}, "unknown scopes"),
        ({"oauth__scopes": ["orders:read", "settings:read"]}, "unknown scopes"),
        ({"oauth__scopes": ["orders:read", "settings:write"]}, "unknown scopes"),
        ({"oauth__scopes": ["orders:read", "risk:write"]}, "unknown scopes"),
        ({"oauth__scopes": ["catalog:read"]}, "order.paid needs orders:read"),
        (
            {"webhooks": [{"event": "order.paid", "url": "https://a.example.com"}]},
            "app.uninstalled",
        ),
        (
            {"webhooks": [{"event": "order.eaten", "url": "https://a.example.com"}]},
            "unknown webhook event",
        ),
        ({"developer__privacy_policy_url": DELETE}, "privacy_policy_url"),
        ({"pricing": {"model": "recurring"}}, "pricing"),
        ({"pricing": {"model": "external"}}, "pricing.label"),
        ({"category": "gambling"}, "category"),
        (
            {"settings_schema": [{"id": "x", "type": "html", "locales": {}}]},
            "type must be one of",
        ),
        (
            {
                "settings_schema": [
                    {"id": "x", "type": "text", "locales": {"en": {"label": "X"}}}
                ]
            },
            "locales.ar.label",
        ),
        (
            {
                "settings_schema": [
                    {
                        "id": "Bad Id",
                        "type": "text",
                        "locales": {"ar": {"label": "س"}, "en": {"label": "X"}},
                    }
                ]
            },
            "id must match",
        ),
        (
            {
                "settings_schema": [
                    {
                        "id": "m",
                        "type": "select",
                        "locales": {"ar": {"label": "س"}, "en": {"label": "X"}},
                    }
                ]
            },
            "needs options",
        ),
        ({"unexpected": 1}, "Extra inputs"),
    ],
)
def test_each_rule_rejects(changes, message):
    with pytest.raises(ValidationError) as exc:
        ManifestV1.model_validate(bad(**changes))
    assert message in str(exc.value)


UNINSTALL_ONLY = [{"event": "app.uninstalled", "url": "https://app.example.com/h"}]


@pytest.mark.parametrize(
    "scope", ["customers:read", "orders:read", "risk:read", "messages:read"]
)
def test_personal_data_read_scopes_need_a_privacy_policy(scope):
    m = bad(**{
        "oauth__scopes": [scope],
        "webhooks": UNINSTALL_ONLY,
        "developer__privacy_policy_url": DELETE,
    })
    with pytest.raises(ValidationError, match="privacy_policy_url"):
        ManifestV1.model_validate(m)


def test_a_read_only_app_needs_no_privacy_policy():
    ManifestV1.model_validate(
        bad(**{
            "oauth__scopes": ["catalog:read"],
            "webhooks": [
                {"event": "product.updated", "url": "https://app.example.com/h"},
                *UNINSTALL_ONLY,
            ],
            "developer__privacy_policy_url": DELETE,
        })
    )


def test_an_event_scope_may_be_optional():
    """The subscription is only made if the merchant grants it (token exchange)."""
    ManifestV1.model_validate(
        bad(**{
            "oauth__scopes": ["catalog:read"],
            "oauth__optional_scopes": ["orders:read"],
        })
    )


def test_projection_renders_through_the_existing_listing_reader():
    m = ManifestV1.model_validate(GOOD).model_dump(mode="json", by_alias=True)
    listing_manifest = to_listing_manifest(m, developer_name="Bosta Co")
    listing = _listing(listing_manifest)
    assert listing.app_locales["ar"]["name"] == "مزامنة بوسطة"
    assert listing.developer == {
        "name": "Bosta Co",
        "url": None,
        "support_email": "help@example.com",
        "is_first_party": False,
    }
    assert listing.pricing["locales"]["ar"]["label"] == "مجاني"
    assert listing.screenshots[0]["url"] == "https://cdn.example.com/1.png"
    assert listing_manifest["settings_schema"] == GOOD["settings_schema"]


def test_change_type_ranks_what_the_reviewer_must_check():
    base = copy.deepcopy(GOOD)
    assert change_type(base, None) == "new_app"
    assert (
        change_type(bad(**{"oauth__scopes": ["orders:read", "customers:read"]}), base)
        == "new_scopes"
    )
    assert change_type(bad(app_url="https://other.example.com"), base) == "urls"
    assert change_type(bad(tagline={"ar": "جديد", "en": "New"}), base) == "listing_only"


def test_semver_orders_numerically():
    assert semver_key("1.10.0") > semver_key("1.9.9")


def test_settings_validation():
    schema = GOOD["settings_schema"]
    assert (
        validate_settings({"auto_sync": True, "mode": "fast"}, schema, strict_keys=True)
        == {}
    )
    errors = validate_settings(
        {"auto_sync": "yes", "mode": "slow", "extra": 1}, schema, strict_keys=True
    )
    assert set(errors) == {"auto_sync", "mode", "extra"}
    # First-party apps keep their legacy keys.
    assert validate_settings({"legacy": 1}, schema, strict_keys=False) == {}


def test_an_install_only_subscribes_to_events_it_was_granted():
    """Webhooks are data too: without orders:read, no order events."""
    hooks = [
        {"event": "order.paid", "url": "https://a.example.com/h"},
        {"event": "order.created", "url": "https://a.example.com/h"},
        {"event": "product.updated", "url": "https://b.example.com/h"},
        {"event": "app.uninstalled", "url": "https://a.example.com/h"},
        {"event": "store.redact", "url": "https://a.example.com/h"},
    ]
    assert app_subscriptions(hooks, ["catalog:read"]) == {
        "https://b.example.com/h": ["product.updated"]
    }
    assert app_subscriptions(hooks, ["orders:read", "catalog:read"]) == {
        "https://a.example.com/h": ["order.created", "order.paid"],
        "https://b.example.com/h": ["product.updated"],
    }
    # orders:write alone doesn't read orders, so it doesn't receive them.
    assert app_subscriptions(hooks, ["orders:write"]) == {}
    assert app_subscriptions(hooks, None) == {}


# ─── Paid apps (Phase 7) ───────────────────────────────────────────


RECURRING = {
    "model": "recurring",
    "price_cents": 9900,
    "cycle": "monthly",
}


def test_a_recurring_price_is_valid_and_reaches_the_listing():
    m = ManifestV1.model_validate(bad(**{"pricing": RECURRING})).model_dump(
        mode="json", by_alias=True
    )
    listing = to_listing_manifest(m, developer_name="Bosta Sync Co")["pricing"]
    assert listing["plan"] == "recurring"
    assert (listing["price_cents"], listing["cycle"], listing["currency"]) == (
        9900,
        "monthly",
        "EGP",
    )
    assert listing["locales"]["en"]["label"] == "EGP 99 / month"
    # DESIGN.md § 5: Arabic amounts use Arabic-Indic digits, like ar-EG.
    assert listing["locales"]["ar"]["label"] == "٩٩ ج.م في الشهر"


def test_the_arabic_price_uses_arabic_indic_digits_and_separators():
    from src.application.services.app_manifest import price_label

    label = price_label({
        "model": "recurring",
        "price_cents": 125_050,
        "cycle": "annual",
    })
    assert label == {"ar": "١٬٢٥٠٫٥٠ ج.م في السنة", "en": "EGP 1,250.50 / year"}


@pytest.mark.parametrize(
    ("pricing", "message"),
    [
        ({"model": "recurring", "cycle": "monthly"}, "price_cents"),
        ({"model": "recurring", "price_cents": 9900}, "cycle"),
        ({"model": "free", "price_cents": 9900}, "recurring only"),
        ({**RECURRING, "price_cents": 100}, "greater than or equal"),
        ({**RECURRING, "cycle": "weekly"}, "cycle"),
        ({**RECURRING, "currency": "USD"}, "currency"),
    ],
)
def test_bad_prices_are_rejected(pricing, message):
    with pytest.raises(ValidationError) as exc:
        ManifestV1.model_validate(bad(**{"pricing": pricing}))
    assert message in str(exc.value)


def test_a_price_change_is_its_own_review_type():
    base = ManifestV1.model_validate(bad(**{"pricing": RECURRING})).model_dump(
        mode="json"
    )
    raised = {**base, "pricing": {**base["pricing"], "price_cents": 19900}}
    assert change_type(raised, base) == "pricing"


CARRIER = {
    "create_shipment_url": "https://app.example.com/ship",
    "rates_url": "https://app.example.com/rates",
    "labels": True,
}


def test_a_shipping_app_may_declare_a_carrier():
    m = ManifestV1.model_validate({**GOOD, "carrier": CARRIER}).model_dump(
        by_alias=True, exclude_none=True
    )
    listing = to_listing_manifest(m, developer_name="Dev")
    assert listing["app"]["carrier"]["rates_url"] == CARRIER["rates_url"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"category": "marketing"}, "only for category shipping"),
        ({"carrier": {**CARRIER, "rates_url": "http://app.example.com/r"}}, "https"),
        (
            {"oauth": {**GOOD["oauth"], "scopes": ["orders:read"]}},
            "a carrier needs oauth.scopes: orders:write",
        ),
    ],
)
def test_carrier_rules_reject(changes, message):
    with pytest.raises(ValidationError, match=message):
        ManifestV1.model_validate({**GOOD, "carrier": CARRIER, **changes})


def test_a_carrier_url_change_is_a_url_review():
    base = {**GOOD, "carrier": CARRIER}
    moved = {**base, "carrier": {**CARRIER, "rates_url": "https://new.example.com/r"}}
    assert change_type(moved, base) == "urls"
