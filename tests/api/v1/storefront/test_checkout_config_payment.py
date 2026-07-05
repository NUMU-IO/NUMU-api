"""GET /storefront/store/{id}/checkout-config — payment-config shape.

Commerce-correctness Phase 1. Verifies the endpoint emits the structured
payment block (payment_methods / cod / currency / saved_cards_enabled) built
from the merchant's existing payment settings + COD deposit policy, alongside
the legacy checkout-field config. Calls the route handler directly with a fake
store repo so no Postgres is required.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.api.v1.routes.storefront.checkout_config import get_public_checkout_config
from src.core.value_objects.money import Currency


class _FakeStoreRepo:
    def __init__(self, store):
        self._store = store

    async def get_by_id(self, store_id):
        return self._store


def _store(settings: dict, *, country="EG", currency=Currency.EGP):
    return SimpleNamespace(
        id=uuid4(),
        settings=settings,
        country=country,
        default_currency=currency,
    )


@pytest.mark.asyncio
async def test_checkout_config_payment_shape_basic():
    settings = {
        "payment": {
            "cod": {"enabled": True, "is_configured": True},
            "paymob": {"enabled": True, "is_configured": True},
            "fawry": {"enabled": False, "is_configured": True},
        }
    }
    store = _store(settings)
    resp = await get_public_checkout_config(
        store_id=store.id, store_repo=_FakeStoreRepo(store)
    )
    data = resp.data

    # Required structured keys present.
    assert "payment_methods" in data
    assert "cod" in data
    assert "currency" in data
    assert "saved_cards_enabled" in data

    codes = {m["code"] for m in data["payment_methods"]}
    assert "cod" in codes
    assert "paymob" in codes
    assert "fawry" not in codes  # disabled

    # Each method carries bilingual labels + requires_deposit bool.
    for m in data["payment_methods"]:
        assert set(m.keys()) >= {"code", "label", "label_ar", "requires_deposit"}
        assert isinstance(m["requires_deposit"], bool)

    # COD enabled, no deposit policy → deposit_required False, no gateways.
    assert data["cod"]["enabled"] is True
    assert data["cod"]["deposit_required"] is False
    assert data["cod"]["deposit_gateways"] == []

    assert data["currency"] == "EGP"
    # paymob enabled → saved cards available.
    assert data["saved_cards_enabled"] is True


@pytest.mark.asyncio
async def test_checkout_config_cod_deposit_policy():
    settings = {
        "payment": {
            "cod": {
                "enabled": True,
                "is_configured": True,
                "deposit_policy": {
                    "enabled": True,
                    "amount_cents": 5000,
                    "allowed_gateways": ["paymob", "kashier"],
                },
            },
            "paymob": {"enabled": True, "is_configured": True},
        }
    }
    store = _store(settings)
    resp = await get_public_checkout_config(
        store_id=store.id, store_repo=_FakeStoreRepo(store)
    )
    data = resp.data

    assert data["cod"]["enabled"] is True
    assert data["cod"]["deposit_required"] is True
    assert data["cod"]["deposit_gateways"] == ["paymob", "kashier"]

    cod_method = next(m for m in data["payment_methods"] if m["code"] == "cod")
    assert cod_method["requires_deposit"] is True


@pytest.mark.asyncio
async def test_checkout_config_saved_cards_false_without_card_gateway():
    settings = {
        "payment": {
            "cod": {"enabled": True, "is_configured": True},
            "instapay": {"enabled": True, "is_configured": True},
        }
    }
    store = _store(settings)
    resp = await get_public_checkout_config(
        store_id=store.id, store_repo=_FakeStoreRepo(store)
    )
    data = resp.data
    # No paymob/kashier/moyasar enabled → no saved cards.
    assert data["saved_cards_enabled"] is False


@pytest.mark.asyncio
async def test_checkout_config_still_has_legacy_fields():
    """The structured block is additive — legacy field config still present."""
    store = _store({"payment": {"cod": {"enabled": True, "is_configured": True}}})
    resp = await get_public_checkout_config(
        store_id=store.id, store_repo=_FakeStoreRepo(store)
    )
    data = resp.data
    assert "standard_fields" in data
    assert "enabled_payment_methods" in data  # legacy list preserved


@pytest.mark.asyncio
async def test_checkout_config_surfaces_paymob_applepay_when_enabled():
    """Apple Pay shows as its own method when the merchant enabled it on Paymob."""
    settings = {
        "payment": {
            "paymob": {
                "enabled": True,
                "is_configured": True,
                "apple_pay_enabled": True,
            },
        }
    }
    store = _store(settings)
    resp = await get_public_checkout_config(
        store_id=store.id, store_repo=_FakeStoreRepo(store)
    )
    methods = {m["code"]: m for m in resp.data["payment_methods"]}
    assert "paymob" in methods
    assert "paymob_applepay" in methods
    assert methods["paymob_applepay"]["label"] == "Apple Pay"
    assert methods["paymob_applepay"]["requires_deposit"] is False


@pytest.mark.asyncio
async def test_checkout_config_no_paymob_applepay_without_flag():
    """No Apple Pay option when the merchant hasn't enabled it (opt-in)."""
    settings = {"payment": {"paymob": {"enabled": True, "is_configured": True}}}
    store = _store(settings)
    resp = await get_public_checkout_config(
        store_id=store.id, store_repo=_FakeStoreRepo(store)
    )
    codes = {m["code"] for m in resp.data["payment_methods"]}
    assert "paymob" in codes
    assert "paymob_applepay" not in codes


@pytest.mark.asyncio
async def test_checkout_config_surfaces_kashier_applepay_when_enabled():
    settings = {
        "payment": {
            "kashier": {
                "enabled": True,
                "is_configured": True,
                "apple_pay_enabled": True,
            },
        }
    }
    store = _store(settings)
    resp = await get_public_checkout_config(
        store_id=store.id, store_repo=_FakeStoreRepo(store)
    )
    codes = {m["code"] for m in resp.data["payment_methods"]}
    assert "kashier" in codes
    assert "kashier_applepay" in codes


@pytest.mark.asyncio
async def test_checkout_config_moyasar_label_mentions_apple_pay():
    """KSA/Moyasar option advertises Apple Pay in its label (Phase 0)."""
    settings = {"payment": {"moyasar": {"enabled": True, "is_configured": True}}}
    store = _store(settings, country="SA")
    resp = await get_public_checkout_config(
        store_id=store.id, store_repo=_FakeStoreRepo(store)
    )
    moyasar = next(
        (m for m in resp.data["payment_methods"] if m["code"] == "moyasar"), None
    )
    assert moyasar is not None
    assert "Apple Pay" in moyasar["label"]


@pytest.mark.asyncio
async def test_checkout_config_hides_applepay_when_platform_disabled(monkeypatch):
    """Super-admin master switch off hides Apple Pay even if the store enabled it."""

    async def _disabled(_db):
        return False

    monkeypatch.setattr(
        "src.application.services.platform_flags.is_apple_pay_platform_enabled",
        _disabled,
    )
    settings = {
        "payment": {
            "paymob": {
                "enabled": True,
                "is_configured": True,
                "apple_pay_enabled": True,
            },
        }
    }
    store = _store(settings)
    resp = await get_public_checkout_config(
        store_id=store.id, store_repo=_FakeStoreRepo(store)
    )
    codes = {m["code"] for m in resp.data["payment_methods"]}
    assert "paymob" in codes
    assert "paymob_applepay" not in codes  # gated off by the platform switch
