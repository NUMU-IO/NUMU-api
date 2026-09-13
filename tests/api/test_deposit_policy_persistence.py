"""PATCH /stores/{id}/settings/payment — the COD deposit policy round-trips.

The policy is validated by a Pydantic model but persisted into a JSON settings
blob. When that write listed fields by hand, three fields added to the model
(`mode`, `percent`, `min_order_cents`) were accepted, validated, and then
dropped on save — the merchant got a success toast for a setting that never
persisted, and the next page load showed the old value back.

Calls the route handler directly with fakes, so no Postgres is required.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.api.v1.routes.stores.settings import update_payment_settings
from src.api.v1.schemas.tenant.settings import (
    CodDepositPolicy,
    UpdatePaymentSettingsRequest,
)


class _FakeStoreRepo:
    async def update(self, store):
        return store


class _FakeOnboardingRepo:
    async def get_by_store_id(self, store_id):
        return None


def _store() -> SimpleNamespace:
    """A store with InstaPay live, so the deposit gateway guard passes."""
    return SimpleNamespace(
        id=uuid4(),
        settings={
            "payment": {
                "cod": {"enabled": True, "is_configured": True},
                "instapay": {"enabled": True, "is_configured": True},
            }
        },
    )


async def _save(policy: CodDepositPolicy) -> dict:
    store = _store()
    await update_payment_settings(
        request=UpdatePaymentSettingsRequest(cod_deposit_policy=policy),
        store=store,
        store_repo=_FakeStoreRepo(),
        onboarding_repo=_FakeOnboardingRepo(),
    )
    return store.settings["payment"]["cod"]["deposit_policy"]


@pytest.mark.asyncio
async def test_every_policy_field_is_persisted() -> None:
    """No field of the model may be lost on the way into settings."""
    stored = await _save(
        CodDepositPolicy(
            enabled=True,
            mode="percent",
            percent=40,
            min_order_cents=100_000,
            ttl_minutes=45,
            allowed_gateways=["instapay"],
        )
    )
    assert set(stored) == set(CodDepositPolicy.model_fields)


@pytest.mark.asyncio
async def test_percent_policy_survives_the_round_trip() -> None:
    stored = await _save(
        CodDepositPolicy(
            enabled=True,
            mode="percent",
            percent=40,
            min_order_cents=100_000,
            allowed_gateways=["instapay"],
        )
    )
    assert stored["mode"] == "percent"
    assert stored["percent"] == 40
    assert stored["min_order_cents"] == 100_000


@pytest.mark.asyncio
async def test_fixed_policy_survives_the_round_trip() -> None:
    stored = await _save(
        CodDepositPolicy(
            enabled=True,
            mode="fixed",
            amount_cents=5_000,
            min_order_cents=50_000,
            allowed_gateways=["instapay"],
        )
    )
    assert stored["mode"] == "fixed"
    assert stored["amount_cents"] == 5_000
    assert stored["min_order_cents"] == 50_000
