"""Unit tests — InstaPay subscription receipt submission (OCR → activate/review)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.application.services.wallet_settings import (
    invalidate_wallet_settings_cache,
    update_wallet_settings,
)
from src.application.use_cases.billing.create_instapay_payment import (
    CreateSubscriptionPaymentIntentUseCase,
)
from src.application.use_cases.billing.submit_subscription_payment_proof import (
    SubmitSubscriptionPaymentProofUseCase,
)
from src.core.entities.plan import get_plan_features
from src.core.interfaces.services.storage_service import UploadedFile
from src.infrastructure.database.models.public.billing import BillingInvoiceModel
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.wallet import (
    WalletTopupIntentModel,
    WalletTopupProofModel,
)
from src.infrastructure.external_services.vision import ProofVisionResult

PLATFORM_IPA = "numu@instapay"


def _naive(dt: datetime) -> datetime:
    """SQLite loads DateTime(timezone=True) back naive — normalize for
    equality checks against aware datetimes built in the test."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


@pytest.fixture(autouse=True)
def _fresh_wallet_settings():
    invalidate_wallet_settings_cache()
    yield
    invalidate_wallet_settings_cache()


class FakeStorage:
    def __init__(self):
        self.uploaded: list[str] = []
        self.deleted: list[str] = []

    async def upload_file(self, *, file_content, filename, content_type, bucket):
        self.uploaded.append(filename)
        return UploadedFile(
            key=filename,
            url=f"https://cdn/{filename}",
            size=len(file_content),
            content_type=content_type,
        )

    async def delete_file(self, key):
        self.deleted.append(key)


class FakeVision:
    def __init__(self, result: ProofVisionResult):
        self.result = result

    async def extract(self, image_bytes, *, hint_currency="EGP"):
        return self.result


def _vision_ok(amount_cents: int, *, ipa: str = PLATFORM_IPA, note: str | None = None):
    return FakeVision(
        ProofVisionResult(
            status="ok",
            provider="fake",
            extracted_amount_cents=amount_cents,
            extracted_ipa=ipa,
            extracted_note=note,
            raw_text="fake receipt",
        )
    )


async def _mk_tenant(session, **kw) -> TenantModel:
    tenant = TenantModel(
        id=uuid4(),
        name="Sub Tenant",
        subdomain=f"sub-{uuid4().hex[:8]}",
        plan=kw.pop("plan", "trial"),
        lifecycle_state=kw.pop("lifecycle_state", "trial"),
        **kw,
    )
    session.add(tenant)
    await session.commit()
    return tenant


async def _mk_intent(session, tenant, plan="starter", cycle="monthly"):
    await update_wallet_settings(session, {"instapay_ipa": PLATFORM_IPA})
    await session.commit()
    result = await CreateSubscriptionPaymentIntentUseCase(session).execute(
        tenant=tenant, user_id=uuid4(), plan=plan, billing_cycle=cycle
    )
    await session.commit()
    return result.intent


def _uc(session, vision):
    return SubmitSubscriptionPaymentProofUseCase(
        session=session, storage_service=FakeStorage(), vision_service=vision
    )


async def _submit(session, intent, vision, *, image=b"receipt-bytes", ref=None):
    return await _uc(session, vision).execute(
        tenant_id=intent.tenant_id,
        intent_id=intent.id,
        image_bytes=image,
        image_content_type="image/jpeg",
        transaction_ref=ref or f"tx-{uuid4().hex[:10]}",
    )


@pytest.mark.asyncio
async def test_full_ocr_match_activates_instantly(test_session):
    tenant = await _mk_tenant(test_session)
    intent = await _mk_intent(test_session, tenant)
    price = get_plan_features("starter").monthly_price_piasters

    result = await _submit(
        test_session,
        intent,
        _vision_ok(price, note=f"NUMU Starter {intent.special_reference}"),
    )
    await test_session.commit()

    assert result.proof.status == "auto_approved"
    assert result.activation is not None and result.activation.activated
    assert intent.status == "succeeded"
    assert intent.activated_at is not None

    await test_session.refresh(tenant)
    assert tenant.lifecycle_state == "active"
    assert tenant.plan == "starter"
    assert tenant.billing_cycle == "monthly"
    assert tenant.next_renewal_at is not None
    assert tenant.trial_converted_at is not None

    invoice = (
        await test_session.execute(
            select(BillingInvoiceModel).where(
                BillingInvoiceModel.tenant_id == tenant.id
            )
        )
    ).scalar_one()
    assert invoice.status == "paid"
    assert invoice.amount_cents == price
    assert invoice.subscription_payment_intent_id == intent.id


@pytest.mark.asyncio
async def test_renewal_extends_period_and_clears_past_due(test_session):
    anchor = datetime.now(UTC) + timedelta(days=2)
    tenant = await _mk_tenant(
        test_session,
        plan="starter",
        lifecycle_state="past_due",
        billing_cycle="monthly",
        next_renewal_at=anchor,
        renewal_retry_count=2,
    )
    intent = await _mk_intent(test_session, tenant)
    assert intent.purpose == "renewal"

    price = get_plan_features("starter").monthly_price_piasters
    result = await _submit(test_session, intent, _vision_ok(price))
    await test_session.commit()

    assert result.activation is not None and result.activation.activated
    await test_session.refresh(tenant)
    assert tenant.lifecycle_state == "active"
    assert tenant.renewal_retry_count == 0
    assert _naive(tenant.next_renewal_at) == _naive(anchor + timedelta(days=30))

    invoice = (
        await test_session.execute(
            select(BillingInvoiceModel).where(
                BillingInvoiceModel.tenant_id == tenant.id
            )
        )
    ).scalar_one()
    assert invoice.subscription_payment_intent_id == intent.id
    assert _naive(invoice.period_start) == _naive(anchor)


@pytest.mark.asyncio
async def test_no_ocr_downgrades_to_review(test_session):
    tenant = await _mk_tenant(test_session)
    intent = await _mk_intent(test_session, tenant)

    vision = FakeVision(ProofVisionResult(status="skipped", provider="noop"))
    result = await _submit(test_session, intent, vision)
    await test_session.commit()

    assert result.proof.status == "awaiting_review"
    assert result.activation is None
    assert "ocr_verification_unavailable" in result.proof.auto_approval_block_reasons
    assert intent.status == "under_review"

    await test_session.refresh(tenant)
    assert tenant.lifecycle_state == "trial"  # untouched


@pytest.mark.asyncio
async def test_ocr_amount_mismatch_blocks(test_session):
    tenant = await _mk_tenant(test_session)
    intent = await _mk_intent(test_session, tenant)

    result = await _submit(test_session, intent, _vision_ok(10_000))
    await test_session.commit()

    assert result.proof.status == "awaiting_review"
    assert "ocr_amount_mismatch" in result.proof.auto_approval_block_reasons
    assert intent.status == "under_review"


@pytest.mark.asyncio
async def test_pro_annual_passes_subscription_threshold(test_session):
    """Wallet thresholds (500 EGP) would block every annual payment —
    the subscription config must clear Pro annual (4,990 EGP)."""
    tenant = await _mk_tenant(test_session)
    intent = await _mk_intent(test_session, tenant, plan="pro", cycle="annual")
    price = get_plan_features("pro").annual_price_piasters

    result = await _submit(test_session, intent, _vision_ok(price))
    await test_session.commit()

    assert result.proof.status == "auto_approved"
    assert result.activation is not None and result.activation.activated
    await test_session.refresh(tenant)
    assert tenant.plan == "pro"
    assert tenant.billing_cycle == "annual"


@pytest.mark.asyncio
async def test_cross_pipeline_dedup_against_wallet_proofs(test_session):
    """One receipt can't fund both a wallet top-up and a subscription."""
    tenant = await _mk_tenant(test_session)
    intent = await _mk_intent(test_session, tenant)

    shared_ref = f"tx-{uuid4().hex[:10]}"
    wallet_intent = WalletTopupIntentModel(
        tenant_id=tenant.id,
        method="instapay",
        amount_cents=10_000,
        currency="EGP",
        status="succeeded",
        special_reference=f"WT-{uuid4().hex[:6].upper()}",
        display_destination=PLATFORM_IPA,
    )
    test_session.add(wallet_intent)
    await test_session.flush()
    test_session.add(
        WalletTopupProofModel(
            tenant_id=tenant.id,
            topup_intent_id=wallet_intent.id,
            proof_image_key="wallet-topups/x.bin",
            proof_image_hash=uuid4().bytes,
            transaction_ref=shared_ref,
            status="approved",
        )
    )
    await test_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _submit(
            test_session,
            intent,
            _vision_ok(intent.amount_cents),
            ref=shared_ref,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_expired_intent_410(test_session):
    tenant = await _mk_tenant(test_session)
    intent = await _mk_intent(test_session, tenant)
    intent.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await test_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _submit(test_session, intent, _vision_ok(intent.amount_cents))
    assert exc.value.status_code == 410
