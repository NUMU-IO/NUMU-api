"""Billing routes — subscribe, cancel, invoices, discount codes, InstaPay.

POST /api/v1/billing/subscribe
POST /api/v1/billing/cancel
GET  /api/v1/billing/invoices
POST /api/v1/billing/discount-code/validate
GET  /api/v1/billing/plans
POST /api/v1/billing/instapay-intents
GET  /api/v1/billing/instapay-intents
GET  /api/v1/billing/instapay-intents/{intent_id}
POST /api/v1/billing/instapay-intents/{intent_id}/proof
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.services import get_storage_service
from src.api.dependencies.tenant_context import (
    get_owner_tenant,
    get_owner_tenant_or_none,
)
from src.api.responses import SuccessResponse
from src.api.utils.upload_validation import validate_image_upload
from src.application.use_cases.billing.cancel_subscription import (
    CancelSubscriptionUseCase,
)
from src.application.use_cases.billing.subscribe import SubscribeUseCase
from src.core.interfaces.services.storage_service import IStorageService
from src.infrastructure.database.models.public.billing import (
    BillingInvoiceModel,
    DiscountCodeModel,
)
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentIntentModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────


class SubscribeRequest(BaseModel):
    plan: str = Field(description="starter or pro")
    billing_cycle: str = Field("monthly", description="monthly or annual")
    discount_code: str | None = None
    paymob_card_token: str | None = None


class SubscribeResponse(BaseModel):
    tenant_id: str
    plan: str
    billing_cycle: str
    next_renewal_at: str | None
    message: str


class InvoiceResponse(BaseModel):
    id: str
    period_start: str
    period_end: str
    amount_cents: int
    currency: str
    status: str
    discount_amount_cents: int
    paid_at: str | None
    created_at: str


class ValidateDiscountRequest(BaseModel):
    code: str
    plan: str


class ValidateDiscountResponse(BaseModel):
    valid: bool
    type: str | None = None
    value: int | None = None
    description: str | None = None
    message: str


# ─── Routes ───────────────────────────────────────────────────────────────


@router.post(
    "/billing/subscribe",
    response_model=SuccessResponse[SubscribeResponse],
    summary="Subscribe to a paid plan",
    operation_id="subscribe",
)
async def subscribe(
    request: SubscribeRequest,
    tenant: Annotated[TenantModel, Depends(get_owner_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        use_case = SubscribeUseCase(db)
        result = await use_case.execute(
            tenant_id=tenant.id,
            plan=request.plan,
            billing_cycle=request.billing_cycle,
            discount_code=request.discount_code,
            paymob_card_token=request.paymob_card_token,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # POST-COMMIT gate-cache invalidation: the use case's own best-effort
    # invalidation runs pre-commit, so a concurrent gate read could re-cache
    # the old (not-live) state for up to the 60s TTL. Dropping the key again
    # here — after the commit is durable — guarantees the storefront opens
    # within seconds of choosing a plan.
    from src.application.services.wallet_service import WalletService

    await WalletService(db).invalidate_cache(tenant.id)

    return SuccessResponse(
        data=SubscribeResponse(
            tenant_id=str(result.id),
            plan=result.plan,
            billing_cycle=result.billing_cycle or "monthly",
            next_renewal_at=result.next_renewal_at.isoformat()
            if result.next_renewal_at
            else None,
            message="Subscription activated successfully.",
        ),
        message="Subscribed",
    )


@router.post(
    "/billing/cancel",
    response_model=SuccessResponse[dict],
    summary="Cancel subscription",
    operation_id="cancel_subscription",
)
async def cancel_subscription(
    tenant: Annotated[TenantModel, Depends(get_owner_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        use_case = CancelSubscriptionUseCase(db)
        await use_case.execute(tenant.id)
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return SuccessResponse(
        data={"cancelled": True},
        message="Subscription cancelled. Your store will remain accessible for 30 more days.",
    )


@router.get(
    "/billing/invoices",
    response_model=SuccessResponse[list[InvoiceResponse]],
    summary="List invoices",
    operation_id="list_invoices",
)
async def list_invoices(
    tenant: Annotated[TenantModel | None, Depends(get_owner_tenant_or_none)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Contract predates the tenant resolver: a user without a tenant yet
    # (registered, no store created) gets an empty list, not a 404.
    if tenant is None:
        return SuccessResponse(data=[], message="No invoices")

    inv_q = (
        select(BillingInvoiceModel)
        .where(BillingInvoiceModel.tenant_id == tenant.id)
        .order_by(BillingInvoiceModel.created_at.desc())
        .limit(50)
    )
    invoices = (await db.execute(inv_q)).scalars().all()

    return SuccessResponse(
        data=[
            InvoiceResponse(
                id=str(inv.id),
                period_start=str(inv.period_start),
                period_end=str(inv.period_end),
                amount_cents=inv.amount_cents,
                currency=inv.currency,
                status=inv.status,
                discount_amount_cents=inv.discount_amount_cents,
                paid_at=str(inv.paid_at) if inv.paid_at else None,
                created_at=str(inv.created_at),
            )
            for inv in invoices
        ],
        message="Invoices retrieved",
    )


# ─── InstaPay subscription payments ──────────────────────────────────────


class PlanCatalogEntry(BaseModel):
    plan: str
    display_name: str
    monthly_price_cents: int
    annual_price_cents: int


class SubscriptionPaymentIntentResponse(BaseModel):
    id: str
    plan: str
    billing_cycle: str
    purpose: str
    amount_cents: int
    currency: str
    status: str
    reference_code: str
    destination: str | None
    destination_label: str | None
    qr_payload: str | None
    expires_at: datetime | None
    rejection_reason: str | None = None
    created_at: datetime | None = None


class CreateInstapayIntentRequest(BaseModel):
    plan: str = Field(description="starter or pro")
    billing_cycle: str = Field("monthly", description="monthly or annual")


def _intent_response(
    intent: SubscriptionPaymentIntentModel,
    *,
    destination_label: str | None = None,
) -> SubscriptionPaymentIntentResponse:
    return SubscriptionPaymentIntentResponse(
        id=str(intent.id),
        plan=intent.plan_key,
        billing_cycle=intent.billing_cycle,
        purpose=intent.purpose,
        amount_cents=intent.amount_cents,
        currency=intent.currency,
        status=intent.status,
        reference_code=intent.special_reference,
        destination=intent.display_destination,
        destination_label=destination_label or intent.display_destination,
        qr_payload=intent.qr_payload,
        expires_at=intent.expires_at,
        # Set by a reject decision; survives the intent reopening so the
        # merchant sees the "why" when retrying.
        rejection_reason=intent.failure_reason,
        created_at=intent.created_at,
    )


@router.get(
    "/billing/plans",
    response_model=SuccessResponse[dict],
    summary="Plan catalog with live prices + current subscription state",
    operation_id="get_billing_plans",
)
async def get_billing_plans(
    tenant: Annotated[TenantModel | None, Depends(get_owner_tenant_or_none)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Single source of truth for what the hub's Billing page renders.

    Prices come from ``get_plan_features`` (admin plan_limits overrides
    already applied in-memory) — NOT from the landing-page pricing
    config, which is display-only.
    """
    from src.application.services.billing_settings import get_billing_settings
    from src.application.services.wallet_settings import get_wallet_settings
    from src.core.entities.plan import get_plan_features
    from src.core.entities.subscription_payment import INSTAPAY_PAYABLE_PLANS
    from src.infrastructure.database.models.public.user import UserModel

    plans = []
    for key in sorted(INSTAPAY_PAYABLE_PLANS):
        f = get_plan_features(key)
        plans.append(
            PlanCatalogEntry(
                plan=key,
                display_name=f.display_name,
                monthly_price_cents=f.monthly_price_piasters,
                annual_price_cents=f.annual_price_piasters,
            )
        )

    admin = await get_wallet_settings(db)
    lifecycle_cfg = await get_billing_settings(db)

    current: dict | None = None
    if tenant is not None:
        now = datetime.now(UTC)
        # Merchant override else the admin-tunable window — the hub's
        # "renewal due — pay now" banner and the reminder email agree.
        effective_days = (
            tenant.renewal_reminder_days
            if tenant.renewal_reminder_days
            else lifecycle_cfg.renewal_warning_days
        )
        renewal_due = tenant.lifecycle_state == "past_due" or (
            tenant.next_renewal_at is not None
            and tenant.next_renewal_at <= now + timedelta(days=effective_days)
        )
        current = {
            "plan": tenant.plan,
            "billing_cycle": tenant.billing_cycle,
            "lifecycle_state": tenant.lifecycle_state,
            "next_renewal_at": (
                tenant.next_renewal_at.isoformat() if tenant.next_renewal_at else None
            ),
            "renewal_due": renewal_due,
            "reminder": {
                "days": tenant.renewal_reminder_days,
                "emails_enabled": not tenant.renewal_reminder_optout,
                "platform_default_days": lifecycle_cfg.renewal_warning_days,
            },
        }

    # Signup plan intent (starter/pro chosen on the landing page) — lets
    # the Billing page preselect what the merchant already said they
    # wanted. Redeemed-at-store-creation only for payg; this closes the
    # loop for the paid tiers.
    plan_intent = (
        await db.execute(select(UserModel.plan_intent).where(UserModel.id == user_id))
    ).scalar_one_or_none()

    return SuccessResponse(
        data={
            "plans": [p.model_dump() for p in plans],
            "current": current,
            "instapay_available": bool(admin.instapay_ipa),
            "plan_intent": plan_intent,
        },
        message="Plans",
    )


class ReminderSettingsRequest(BaseModel):
    """Merchant renewal-reminder prefs. days=null → platform default."""

    days: int | None = Field(default=None, ge=1, le=30)
    emails_enabled: bool = True


@router.put(
    "/billing/reminder-settings",
    response_model=SuccessResponse[dict],
    summary="Set renewal reminder preferences",
    operation_id="update_reminder_settings",
)
async def update_reminder_settings(
    request: ReminderSettingsRequest,
    tenant: Annotated[TenantModel, Depends(get_owner_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant.renewal_reminder_days = request.days
    tenant.renewal_reminder_optout = not request.emails_enabled
    await db.commit()
    return SuccessResponse(
        data={
            "days": tenant.renewal_reminder_days,
            "emails_enabled": not tenant.renewal_reminder_optout,
        },
        message="Reminder settings saved",
    )


@router.get(
    "/billing/instapay-intents",
    response_model=SuccessResponse[list[SubscriptionPaymentIntentResponse]],
    summary="Recent InstaPay subscription payments (status strip)",
    operation_id="list_instapay_intents",
)
async def list_instapay_intents(
    tenant: Annotated[TenantModel, Depends(get_owner_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    intents = (
        (
            await db.execute(
                select(SubscriptionPaymentIntentModel)
                .where(SubscriptionPaymentIntentModel.tenant_id == tenant.id)
                .order_by(SubscriptionPaymentIntentModel.created_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return SuccessResponse(data=[_intent_response(i) for i in intents])


@router.post(
    "/billing/instapay-intents",
    response_model=SuccessResponse[SubscriptionPaymentIntentResponse],
    status_code=201,
    summary="Start an InstaPay subscription payment",
    operation_id="create_instapay_intent",
)
async def create_instapay_intent(
    request: CreateInstapayIntentRequest,
    tenant: Annotated[TenantModel, Depends(get_owner_tenant)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from src.application.services.wallet_settings import get_wallet_settings
    from src.application.use_cases.billing.create_instapay_payment import (
        CreateSubscriptionPaymentIntentUseCase,
    )

    result = await CreateSubscriptionPaymentIntentUseCase(db).execute(
        tenant=tenant,
        user_id=user_id,
        plan=request.plan,
        billing_cycle=request.billing_cycle,
    )
    await db.commit()

    admin = await get_wallet_settings(db)
    return SuccessResponse(
        data=_intent_response(
            result.intent,
            destination_label=admin.instapay_display_name
            or result.intent.display_destination,
        ),
        message="Payment created",
    )


@router.get(
    "/billing/instapay-intents/{intent_id}",
    response_model=SuccessResponse[SubscriptionPaymentIntentResponse],
    summary="Poll an InstaPay subscription payment",
    operation_id="get_instapay_intent",
)
async def get_instapay_intent(
    intent_id: UUID,
    tenant: Annotated[TenantModel, Depends(get_owner_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    intent = (
        await db.execute(
            select(SubscriptionPaymentIntentModel).where(
                SubscriptionPaymentIntentModel.id == intent_id,
                SubscriptionPaymentIntentModel.tenant_id == tenant.id,
            )
        )
    ).scalar_one_or_none()
    if intent is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return SuccessResponse(data=_intent_response(intent))


@router.post(
    "/billing/instapay-intents/{intent_id}/proof",
    response_model=SuccessResponse[dict],
    status_code=201,
    summary="Upload the transfer receipt for a subscription payment",
    operation_id="submit_instapay_intent_proof",
)
async def submit_instapay_intent_proof(
    intent_id: UUID,
    transaction_ref: Annotated[str, Form(min_length=3, max_length=64)],
    file: Annotated[UploadFile, File(description="Transfer receipt")],
    tenant: Annotated[TenantModel, Depends(get_owner_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_service: Annotated[IStorageService, Depends(get_storage_service)],
    declared_amount_cents: Annotated[int | None, Form()] = None,
):
    from src.application.use_cases.billing.submit_subscription_payment_proof import (
        SubmitSubscriptionPaymentProofUseCase,
    )
    from src.infrastructure.external_services.image.proof_sanitizer import (
        ProofImageDecodeError,
        sanitize_proof_image,
    )

    raw_bytes = await validate_image_upload(file)
    try:
        sanitized = sanitize_proof_image(
            raw_bytes,
            content_type=file.content_type or "application/octet-stream",
        )
    except ProofImageDecodeError as exc:
        raise HTTPException(
            status_code=415,
            detail=f"Could not decode the uploaded image: {exc}",
        ) from exc

    use_case = SubmitSubscriptionPaymentProofUseCase(
        session=db, storage_service=storage_service
    )
    result = await use_case.execute(
        tenant_id=tenant.id,
        intent_id=intent_id,
        image_bytes=sanitized.bytes,
        image_content_type=sanitized.content_type,
        transaction_ref=transaction_ref,
        declared_amount_cents=declared_amount_cents,
        image_perceptual_hash=sanitized.perceptual_hash,
    )
    await db.commit()

    activated = bool(result.activation and result.activation.activated)
    if activated:
        # POST-COMMIT gate-cache invalidation (same rationale as
        # /billing/subscribe): the storefront's go-live gate must see the
        # new plan within seconds, not after the 60s cache TTL.
        from src.application.services.wallet_service import WalletService

        await WalletService(db).invalidate_cache(tenant.id)

    return SuccessResponse(
        data={
            "proof_id": str(result.proof.id),
            "intent_id": str(result.intent.id),
            "status": result.proof.status,
            "intent_status": result.intent.status,
            "reasons": result.decision.reasons,
            "activated": activated,
            "plan": result.intent.plan_key,
            "billing_cycle": result.intent.billing_cycle,
            "next_renewal_at": (
                result.activation.next_renewal_at.isoformat()
                if result.activation and result.activation.next_renewal_at
                else None
            ),
        },
        message=(
            "Subscription activated"
            if activated
            else "Receipt received — verification in progress"
        ),
    )


@router.post(
    "/billing/discount-code/validate",
    response_model=SuccessResponse[ValidateDiscountResponse],
    summary="Validate a discount code",
    operation_id="validate_discount_code",
)
async def validate_discount_code(
    request: ValidateDiscountRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from datetime import UTC, datetime

    q = select(DiscountCodeModel).where(DiscountCodeModel.code == request.code.upper())
    dc = (await db.execute(q)).scalar_one_or_none()

    if not dc:
        return SuccessResponse(
            data=ValidateDiscountResponse(valid=False, message="Invalid code."),
            message="Invalid",
        )

    now = datetime.now(UTC)
    if dc.valid_until and now > dc.valid_until:
        return SuccessResponse(
            data=ValidateDiscountResponse(valid=False, message="Code has expired."),
            message="Expired",
        )
    if dc.max_uses and dc.current_uses >= dc.max_uses:
        return SuccessResponse(
            data=ValidateDiscountResponse(valid=False, message="Code fully redeemed."),
            message="Redeemed",
        )
    if dc.applies_to_plans and request.plan not in dc.applies_to_plans:
        return SuccessResponse(
            data=ValidateDiscountResponse(
                valid=False, message=f"Code does not apply to {request.plan}."
            ),
            message="Not applicable",
        )

    return SuccessResponse(
        data=ValidateDiscountResponse(
            valid=True,
            type=dc.type,
            value=dc.value,
            description=dc.description,
            message="Code is valid!",
        ),
        message="Valid",
    )
