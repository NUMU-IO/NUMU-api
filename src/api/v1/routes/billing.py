"""Billing routes — subscribe, cancel, invoices, discount codes.

POST /api/v1/billing/subscribe
POST /api/v1/billing/cancel
GET  /api/v1/billing/invoices
POST /api/v1/billing/discount-code/validate
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.use_cases.billing.cancel_subscription import (
    CancelSubscriptionUseCase,
)
from src.application.use_cases.billing.subscribe import SubscribeUseCase
from src.infrastructure.database.models.public.billing import (
    BillingInvoiceModel,
    DiscountCodeModel,
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
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Resolve tenant
    q = select(TenantModel).where(TenantModel.owner_id == user_id)
    tenant = (await db.execute(q)).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="No tenant found")

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
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    q = select(TenantModel).where(TenantModel.owner_id == user_id)
    tenant = (await db.execute(q)).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="No tenant found")

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
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    q = select(TenantModel).where(TenantModel.owner_id == user_id)
    tenant = (await db.execute(q)).scalar_one_or_none()
    if not tenant:
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
