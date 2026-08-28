"""Referral system routes.

GET  /api/v1/referrals/my-code       — get/generate referral code
GET  /api/v1/referrals/my-referrals  — list referred merchants + earnings
POST /api/v1/referrals/apply         — apply a referral code during signup
"""

import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.public.referral import (
    MerchantReferralModel,
    ReferralCommissionModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────


class ReferralCodeResponse(BaseModel):
    referral_code: str
    referral_link: str


class ReferralSummary(BaseModel):
    total_earned_cents: int
    confirmed_cents: int
    pending_cents: int
    total_referrals: int
    tier: str  # bronze, silver, gold, diamond


class ReferredMerchant(BaseModel):
    tenant_name: str
    subdomain: str
    referral_date: str
    orders: int
    commission_earned_cents: int


class MyReferralsResponse(BaseModel):
    summary: ReferralSummary
    referrals: list[ReferredMerchant]


class ApplyReferralRequest(BaseModel):
    referral_code: str = Field(max_length=20)


# ─── Helpers ──────────────────────────────────────────────────────────────


def _tier(count: int) -> str:
    if count >= 11:
        return "diamond"
    if count >= 6:
        return "gold"
    if count >= 3:
        return "silver"
    return "bronze"


def _tier_rate(tier: str) -> float:
    return {"bronze": 0.05, "silver": 0.06, "gold": 0.07, "diamond": 0.08}.get(
        tier, 0.05
    )


# ─── Routes ───────────────────────────────────────────────────────────────


@router.get(
    "/referrals/my-code",
    response_model=SuccessResponse[ReferralCodeResponse],
    summary="Get or generate my referral code",
    operation_id="get_referral_code",
)
async def get_referral_code(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant = (
        await db.execute(select(TenantModel).where(TenantModel.owner_id == user_id))
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="No tenant found")

    # Check if this tenant has any referral rows as referrer — derive code from first one
    q = (
        select(MerchantReferralModel.referral_code)
        .where(MerchantReferralModel.referrer_tenant_id == tenant.id)
        .limit(1)
    )
    existing_code = (await db.execute(q)).scalar_one_or_none()

    if existing_code:
        code = existing_code
    else:
        # Generate a new code: STORENAME-NUMU-XXXX
        safe_name = tenant.subdomain.upper().replace("-", "")[:8]
        suffix = secrets.token_hex(2).upper()
        code = f"{safe_name}-NUMU-{suffix}"

    return SuccessResponse(
        data=ReferralCodeResponse(
            referral_code=code,
            referral_link=f"https://numueg.app/signup?ref={code}",
        ),
        message="Referral code retrieved",
    )


@router.get(
    "/referrals/my-referrals",
    response_model=SuccessResponse[MyReferralsResponse],
    summary="List my referrals and earnings",
    operation_id="get_my_referrals",
)
async def get_my_referrals(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant = (
        await db.execute(select(TenantModel).where(TenantModel.owner_id == user_id))
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="No tenant found")

    # Fetch all referrals where I'm the referrer
    refs_q = (
        select(MerchantReferralModel)
        .where(MerchantReferralModel.referrer_tenant_id == tenant.id)
        .order_by(MerchantReferralModel.created_at.desc())
    )
    refs = (await db.execute(refs_q)).scalars().all()

    total_count = len(refs)
    tier = _tier(total_count)
    ref_ids = [r.id for r in refs]

    # Referred tenant names, in one query rather than one per referral.
    names: dict[UUID, TenantModel] = {}
    if refs:
        referred_rows = (
            await db.execute(
                select(TenantModel).where(
                    TenantModel.id.in_([r.referred_tenant_id for r in refs])
                )
            )
        ).scalars()
        names = {t.id: t for t in referred_rows}

    # Per-referral commission counts and sums, also in one query. This used to
    # be two round trips inside the loop, so a merchant with 40 referrals paid
    # 80 sequential queries to open the page.
    #
    # Everything below is derived from the commission ledger, including the
    # totals. The summary previously took its total from the denormalised
    # `total_commission_earned_cents` column on the referral row while the
    # table rows summed the ledger: any drift between the two showed as a
    # header that disagreed with the rows beneath it, and — because pending
    # was computed as (denormalised total − ledger confirmed) — could render
    # a NEGATIVE amount pending.
    per_ref: dict[UUID, tuple[int, int, int]] = {}
    if ref_ids:
        agg_rows = await db.execute(
            select(
                ReferralCommissionModel.referral_id,
                func.count(ReferralCommissionModel.id),
                func.coalesce(func.sum(ReferralCommissionModel.commission_cents), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                ReferralCommissionModel.status == "confirmed",
                                ReferralCommissionModel.commission_cents,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .where(ReferralCommissionModel.referral_id.in_(ref_ids))
            .group_by(ReferralCommissionModel.referral_id)
        )
        per_ref = {row[0]: (row[1], row[2], row[3]) for row in agg_rows.all()}

    referral_list = []
    for ref in refs:
        orders, earned, _confirmed = per_ref.get(ref.id, (0, 0, 0))
        referred = names.get(ref.referred_tenant_id)
        referral_list.append(
            ReferredMerchant(
                tenant_name=referred.name if referred else "Unknown",
                subdomain=referred.subdomain if referred else "",
                # isoformat, not str(): str() renders "2026-08-27 14:23:23+00:00"
                # with a space, which `new Date()` rejects outright on Safari —
                # the hub then printed "Invalid Date" in the referral table.
                referral_date=ref.created_at.isoformat(),
                orders=orders,
                commission_earned_cents=earned,
            )
        )

    total_earned = sum(v[1] for v in per_ref.values())
    confirmed = sum(v[2] for v in per_ref.values())

    return SuccessResponse(
        data=MyReferralsResponse(
            summary=ReferralSummary(
                total_earned_cents=total_earned,
                confirmed_cents=confirmed,
                # Both sides now come from the same ledger, so this cannot go
                # negative; max() is a belt-and-braces guard, not a fudge.
                pending_cents=max(0, total_earned - confirmed),
                total_referrals=total_count,
                tier=tier,
            ),
            referrals=referral_list,
        ),
        message="Referrals retrieved",
    )


@router.post(
    "/referrals/apply",
    response_model=SuccessResponse[dict],
    summary="Apply a referral code during signup",
    operation_id="apply_referral_code",
)
async def apply_referral_code(
    request: ApplyReferralRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from datetime import UTC, datetime, timedelta

    # Find the current user's tenant
    tenant = (
        await db.execute(select(TenantModel).where(TenantModel.owner_id == user_id))
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="No tenant found")

    # Check if already referred
    existing = (
        await db.execute(
            select(MerchantReferralModel).where(
                MerchantReferralModel.referred_tenant_id == tenant.id
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409, detail="This store has already been referred."
        )

    # Find the referrer by code — search across all referral rows
    referrer_q = (
        select(MerchantReferralModel.referrer_tenant_id)
        .where(MerchantReferralModel.referral_code == request.referral_code)
        .limit(1)
    )
    referrer_tenant_id = (await db.execute(referrer_q)).scalar_one_or_none()

    if not referrer_tenant_id:
        # Code might be new — try to find a tenant whose subdomain matches the prefix
        code_parts = request.referral_code.split("-NUMU-")
        if code_parts:
            prefix = code_parts[0].lower()
            referrer = (
                await db.execute(
                    select(TenantModel).where(
                        TenantModel.subdomain.ilike(f"%{prefix}%")
                    )
                )
            ).scalar_one_or_none()
            if referrer:
                referrer_tenant_id = referrer.id

    if not referrer_tenant_id:
        raise HTTPException(status_code=404, detail="Invalid referral code.")

    if referrer_tenant_id == tenant.id:
        raise HTTPException(status_code=422, detail="Cannot refer yourself.")

    # Count referrer's existing referrals for tier
    count_q = select(func.count(MerchantReferralModel.id)).where(
        MerchantReferralModel.referrer_tenant_id == referrer_tenant_id
    )
    count = (await db.execute(count_q)).scalar() or 0
    tier = _tier(count + 1)
    rate = _tier_rate(tier)

    referral = MerchantReferralModel(
        referrer_tenant_id=referrer_tenant_id,
        referred_tenant_id=tenant.id,
        referral_code=request.referral_code,
        status="pending",
        commission_rate=rate,
        commission_expires_at=datetime.now(UTC) + timedelta(days=365),
    )
    db.add(referral)
    await db.commit()

    logger.info(
        "referral_applied",
        extra={
            "referrer_tenant_id": str(referrer_tenant_id),
            "referred_tenant_id": str(tenant.id),
            "code": request.referral_code,
        },
    )

    return SuccessResponse(
        data={"applied": True}, message="Referral code applied successfully."
    )
