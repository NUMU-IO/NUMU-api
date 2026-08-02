"""Unit tests — admin review of subscription receipts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.application.use_cases.billing.activate_instapay_subscription import (
    activate_verified_subscription_payment,
)
from src.application.use_cases.billing.review_subscription_payment_proof import (
    ReviewSubscriptionPaymentProofUseCase,
)
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentIntentModel,
    SubscriptionPaymentProofModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel


async def _mk_tenant(session, **kw) -> TenantModel:
    tenant = TenantModel(
        id=uuid4(),
        name="Review Tenant",
        subdomain=f"rev-{uuid4().hex[:8]}",
        plan=kw.pop("plan", "trial"),
        lifecycle_state=kw.pop("lifecycle_state", "trial"),
        **kw,
    )
    session.add(tenant)
    await session.commit()
    return tenant


async def _mk_under_review(session, tenant, plan="starter", cycle="monthly"):
    """Seed the state submit leaves after a soft-block: intent
    under_review + proof awaiting_review."""
    intent = SubscriptionPaymentIntentModel(
        tenant_id=tenant.id,
        plan_key=plan,
        billing_cycle=cycle,
        purpose="new_subscription",
        amount_cents=25_000,
        currency="EGP",
        status="under_review",
        special_reference=f"SUB-{uuid4().hex[:6].upper()}",
        display_destination="numu@instapay",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(intent)
    await session.flush()
    proof = SubscriptionPaymentProofModel(
        tenant_id=tenant.id,
        intent_id=intent.id,
        proof_image_key=f"subscription-payments/{tenant.id}/x.bin",
        proof_image_hash=uuid4().bytes,
        transaction_ref=f"tx-{uuid4().hex[:10]}",
        status="awaiting_review",
        auto_approval_block_reasons=["ocr_verification_unavailable"],
    )
    session.add(proof)
    await session.commit()
    return intent, proof


@pytest.mark.asyncio
async def test_approve_activates_once_and_double_approve_409(test_session):
    tenant = await _mk_tenant(test_session)
    intent, proof = await _mk_under_review(test_session, tenant)

    uc = ReviewSubscriptionPaymentProofUseCase(test_session)
    result = await uc.approve(proof_id=proof.id, admin_user_id=uuid4())
    await test_session.commit()

    assert result.proof.status == "approved"
    assert result.activation is not None and result.activation.activated
    assert intent.status == "succeeded"
    await test_session.refresh(tenant)
    assert tenant.lifecycle_state == "active"
    assert tenant.plan == "starter"

    # Double-approve: proof is no longer awaiting_review → 409.
    with pytest.raises(HTTPException) as exc:
        await uc.approve(proof_id=proof.id, admin_user_id=uuid4())
    assert exc.value.status_code == 409

    # Direct activation replay is a logged no-op (intent terminal).
    replay = await activate_verified_subscription_payment(test_session, intent=intent)
    assert replay.activated is False


@pytest.mark.asyncio
async def test_reject_reopens_intent_with_reason(test_session):
    tenant = await _mk_tenant(test_session)
    intent, proof = await _mk_under_review(test_session, tenant)

    uc = ReviewSubscriptionPaymentProofUseCase(test_session)
    result = await uc.reject(
        proof_id=proof.id, admin_user_id=uuid4(), reason="Amount not received"
    )
    await test_session.commit()

    assert result.proof.status == "rejected"
    assert result.proof.rejection_reason == "Amount not received"
    assert intent.status == "awaiting_proof"
    assert intent.failure_reason == "Amount not received"
    await test_session.refresh(tenant)
    assert tenant.lifecycle_state == "trial"  # untouched
