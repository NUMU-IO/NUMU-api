"""PATCH /stores/{id}/settings/payment — a gateway can always be switched off.

The toggles refused any change to a gateway without credentials, including
turning it OFF. A store left with "enabled but not configured" could not get
out of that state, and the storefront kept offering the dead gateway to
shoppers. Enabling still requires a configured gateway.

Calls the route handler directly with fakes, so no Postgres is required.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.v1.routes.stores.settings import (
    _get_default_payment_settings,
    update_payment_settings,
)
from src.api.v1.schemas.tenant.settings import UpdatePaymentSettingsRequest


class _FakeStoreRepo:
    async def update(self, store):
        return store


class _FakeOnboardingRepo:
    async def get_by_store_id(self, store_id):
        return None


GATEWAYS = [
    "paymob",
    "fawry",
    "fawaterak",
    "instapay",
    "moyasar",
    "vodafone_cash",
    "bank_transfer",
]


def _store(gateway: str, *, enabled: bool) -> SimpleNamespace:
    payment = _get_default_payment_settings()
    payment.setdefault(gateway, {})
    payment[gateway].update({"enabled": enabled, "is_configured": False})
    return SimpleNamespace(id=uuid4(), settings={"payment": payment})


async def _toggle(store, gateway: str, value: bool) -> None:
    await update_payment_settings(
        request=UpdatePaymentSettingsRequest(**{f"{gateway}_enabled": value}),
        store=store,
        store_repo=_FakeStoreRepo(),
        onboarding_repo=_FakeOnboardingRepo(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway", GATEWAYS)
async def test_unconfigured_gateway_can_be_switched_off(gateway: str) -> None:
    store = _store(gateway, enabled=True)
    await _toggle(store, gateway, False)
    assert store.settings["payment"][gateway]["enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway", GATEWAYS)
async def test_unconfigured_gateway_cannot_be_switched_on(gateway: str) -> None:
    store = _store(gateway, enabled=False)
    with pytest.raises(HTTPException) as caught:
        await _toggle(store, gateway, True)
    assert caught.value.status_code == 400
    assert store.settings["payment"][gateway]["enabled"] is False
