"""Unit tests — creating InstaPay subscription payment intents."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.application.services.wallet_settings import (
    invalidate_wallet_settings_cache,
    update_wallet_settings,
)
from src.application.use_cases.billing.create_instapay_payment import (
    CreateSubscriptionPaymentIntentUseCase,
    classify_purpose,
)
from src.core.entities.plan import get_plan_features
from src.infrastructure.database.models.public.tenant import TenantModel

PLATFORM_IPA = "numu@instapay"


@pytest.fixture(autouse=True)
def _fresh_wallet_settings():
    invalidate_wallet_settings_cache()
    yield
    invalidate_wallet_settings_cache()


async def _mk_tenant(session, plan: str = "trial", **kw) -> TenantModel:
    tenant = TenantModel(
        id=uuid4(),
        name="Sub Tenant",
        subdomain=f"sub-{uuid4().hex[:8]}",
        plan=plan,
        lifecycle_state=kw.pop("lifecycle_state", "trial"),
        **kw,
    )
    session.add(tenant)
    await session.commit()
    return tenant


async def _configure_ipa(session):
    await update_wallet_settings(session, {"instapay_ipa": PLATFORM_IPA})
    await session.commit()


@pytest.mark.asyncio
async def test_create_snapshots_plan_price_per_cycle(test_session):
    await _configure_ipa(test_session)
    tenant = await _mk_tenant(test_session)

    uc = CreateSubscriptionPaymentIntentUseCase(test_session)
    result = await uc.execute(
        tenant=tenant, user_id=uuid4(), plan="starter", billing_cycle="monthly"
    )
    intent = result.intent
    assert intent.amount_cents == get_plan_features("starter").monthly_price_piasters
    assert intent.special_reference.startswith("SUB-")
    assert intent.display_destination == PLATFORM_IPA
    assert intent.status == "awaiting_proof"
    assert intent.purpose == "new_subscription"
    assert intent.qr_payload
    assert intent.expires_at is not None
    await test_session.commit()

    # Annual pro snapshots the annual price (different tenant — one
    # open intent per tenant is enforced).
    tenant2 = await _mk_tenant(test_session)
    result2 = await uc.execute(
        tenant=tenant2, user_id=uuid4(), plan="pro", billing_cycle="annual"
    )
    assert result2.intent.amount_cents == get_plan_features("pro").annual_price_piasters


@pytest.mark.asyncio
async def test_duplicate_open_intent_409_with_id(test_session):
    await _configure_ipa(test_session)
    tenant = await _mk_tenant(test_session)
    uc = CreateSubscriptionPaymentIntentUseCase(test_session)
    first = await uc.execute(
        tenant=tenant, user_id=uuid4(), plan="starter", billing_cycle="monthly"
    )
    await test_session.commit()

    with pytest.raises(HTTPException) as exc:
        await uc.execute(
            tenant=tenant, user_id=uuid4(), plan="pro", billing_cycle="monthly"
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["open_intent_id"] == str(first.intent.id)


@pytest.mark.asyncio
async def test_rejects_unpayable_plans_and_cycles(test_session):
    await _configure_ipa(test_session)
    tenant = await _mk_tenant(test_session)
    uc = CreateSubscriptionPaymentIntentUseCase(test_session)

    for plan in ("payg", "enterprise", "trial", "nonsense"):
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                tenant=tenant, user_id=uuid4(), plan=plan, billing_cycle="monthly"
            )
        assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:
        await uc.execute(
            tenant=tenant, user_id=uuid4(), plan="starter", billing_cycle="weekly"
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_503_when_platform_ipa_unconfigured(test_session):
    tenant = await _mk_tenant(test_session)
    uc = CreateSubscriptionPaymentIntentUseCase(test_session)
    with pytest.raises(HTTPException) as exc:
        await uc.execute(
            tenant=tenant, user_id=uuid4(), plan="starter", billing_cycle="monthly"
        )
    assert exc.value.status_code == 503


def test_classify_purpose():
    def tenant(**kw):
        return TenantModel(
            id=uuid4(),
            name="t",
            subdomain="t",
            plan=kw.pop("plan", "trial"),
            lifecycle_state=kw.pop("lifecycle_state", "trial"),
            **kw,
        )

    # Trial tenant → new subscription regardless of plan.
    assert classify_purpose(tenant(), "starter", "monthly") == "new_subscription"

    # Active same plan+cycle with a renewal anchor → renewal.
    active = tenant(
        plan="starter",
        lifecycle_state="active",
        billing_cycle="monthly",
        next_renewal_at=datetime.now(UTC) + timedelta(days=3),
    )
    assert classify_purpose(active, "starter", "monthly") == "renewal"
    # past_due counts as renewal too (dunning recovery).
    active.lifecycle_state = "past_due"
    assert classify_purpose(active, "starter", "monthly") == "renewal"

    # Different plan or cycle → treated as a new subscription (upgrade).
    assert classify_purpose(active, "pro", "monthly") == "new_subscription"
    assert classify_purpose(active, "starter", "annual") == "new_subscription"
