"""Admin subscription-payment review queue (InstaPay plan payments).

URL: /api/v1/admin/subscription-payments — requires admin auth.

* ``GET  /``                        — receipt review queue + per-status counts
* ``GET  /settings``                — billing lifecycle knobs (warnings + dunning)
* ``PUT  /settings``                — partial patch; ``null`` clears an override
* ``GET  /{proof_id}/image``        — stream the receipt bytes (authenticated)
* ``POST /{proof_id}/approve``      — approve → activate/renew the subscription
* ``POST /{proof_id}/reject``       — reject with a reason; intent reopens

Mirrors ``admin/wallets.py``'s top-up queue so numu-admin can clone the
same card/dialog UI. Plan features/limits/prices live at
``/admin/plan-limits`` (existing route) — the admin UI surfaces both.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.dependencies.services import get_storage_service
from src.api.responses import SuccessResponse
from src.application.use_cases.billing.review_subscription_payment_proof import (
    ReviewSubscriptionPaymentProofUseCase,
)
from src.core.interfaces.services.storage_service import IStorageService
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentIntentModel,
    SubscriptionPaymentProofModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel

logger = logging.getLogger(__name__)
router = APIRouter()


class RejectProofRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class BillingSettingsPatch(BaseModel):
    """Partial patch — absent = keep, null = clear override to default."""

    warning_emails_enabled: bool | None = None
    renewal_warning_days: int | None = Field(default=None, ge=1, le=30)
    trial_warning_days: int | None = Field(default=None, ge=1, le=30)
    dunning_max_retries: int | None = Field(default=None, ge=1, le=10)
    dunning_retry_backoff_hours: int | None = Field(default=None, ge=1, le=168)
    dunning_window_hours: int | None = Field(default=None, ge=0, le=720)


@router.get("/settings", response_model=SuccessResponse[dict])
async def get_billing_lifecycle_settings(
    _admin: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from src.application.services.billing_settings import (
        billing_settings_to_dict,
        get_billing_settings,
    )

    settings = await get_billing_settings(db, use_cache=False)
    return SuccessResponse(data=billing_settings_to_dict(settings))


@router.put("/settings", response_model=SuccessResponse[dict])
async def update_billing_lifecycle_settings(
    request: BillingSettingsPatch,
    _admin: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Tune pre-expiry warning windows and the dunning ladder — live
    immediately (60s settings cache), no deploy."""
    from src.application.services.billing_settings import (
        billing_settings_to_dict,
        update_billing_settings,
    )

    # exclude_unset so "field absent" (keep) differs from "field: null" (clear).
    patch = request.model_dump(exclude_unset=True)
    merged = await update_billing_settings(db, patch)
    await db.commit()
    logger.info(
        "admin_billing_lifecycle_settings_updated", extra={"fields": list(patch)}
    )
    return SuccessResponse(
        data=billing_settings_to_dict(merged),
        message="Billing lifecycle settings updated",
    )


@router.get("", response_model=SuccessResponse[dict])
async def list_subscription_payment_proofs(
    _admin: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str = "awaiting_review",
    skip: int = 0,
    limit: int = 50,
):
    rows = (
        await db.execute(
            select(
                SubscriptionPaymentProofModel,
                SubscriptionPaymentIntentModel,
                TenantModel.name,
            )
            .join(
                SubscriptionPaymentIntentModel,
                SubscriptionPaymentIntentModel.id
                == SubscriptionPaymentProofModel.intent_id,
            )
            .join(
                TenantModel, TenantModel.id == SubscriptionPaymentProofModel.tenant_id
            )
            .where(SubscriptionPaymentProofModel.status == status)
            .order_by(SubscriptionPaymentProofModel.created_at.asc())
            .offset(max(skip, 0))
            .limit(min(max(limit, 1), 200))
        )
    ).all()

    count_rows = (
        await db.execute(
            select(
                SubscriptionPaymentProofModel.status,
                func.count(SubscriptionPaymentProofModel.id),
            ).group_by(SubscriptionPaymentProofModel.status)
        )
    ).all()
    counts = {status_val: int(n) for status_val, n in count_rows}

    proofs = []
    for p, i, tenant_name in rows:
        proofs.append({
            "proof_id": str(p.id),
            "tenant_id": str(p.tenant_id),
            "tenant_name": tenant_name,
            "intent_id": str(i.id),
            "plan": i.plan_key,
            "billing_cycle": i.billing_cycle,
            "purpose": i.purpose,
            "amount_cents": i.amount_cents,
            "reference": i.special_reference,
            "destination": i.display_destination,
            "transaction_ref": p.transaction_ref,
            "declared_amount_cents": p.declared_amount_cents,
            "status": p.status,
            "block_reasons": p.auto_approval_block_reasons,
            "ocr_status": p.ocr_status,
            "ocr_extracted_amount_cents": p.ocr_extracted_amount_cents,
            "ocr_extracted_ipa": p.ocr_extracted_ipa,
            "ocr_extracted_note": p.ocr_extracted_note,
            "rejection_reason": p.rejection_reason,
            # Relative API path streamed below — NOT a presigned R2 URL
            # (presigned URLs for this bucket 403 in-browser; same
            # pattern as the wallet top-up queue).
            "image_url": f"/api/v1/admin/subscription-payments/{p.id}/image",
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return SuccessResponse(data={"proofs": proofs, "counts": counts})


@router.get("/{proof_id}/image")
async def stream_subscription_proof_image(
    proof_id: UUID,
    _admin: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[IStorageService, Depends(get_storage_service)],
):
    """Stream the receipt image bytes (admin-authenticated)."""
    proof = (
        await db.execute(
            select(SubscriptionPaymentProofModel).where(
                SubscriptionPaymentProofModel.id == proof_id
            )
        )
    ).scalar_one_or_none()
    if proof is None:
        raise HTTPException(status_code=404, detail="Proof not found")

    try:
        body, content_type = await storage.get_object_bytes(proof.proof_image_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Proof image is missing")
    except Exception:
        raise HTTPException(status_code=502, detail="Could not fetch the proof image")

    return Response(
        content=body,
        media_type=content_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/{proof_id}/approve", response_model=SuccessResponse[dict])
async def approve_subscription_proof(
    proof_id: UUID,
    admin_user_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await ReviewSubscriptionPaymentProofUseCase(db).approve(
        proof_id=proof_id, admin_user_id=admin_user_id
    )
    await db.commit()

    # POST-COMMIT gate-cache invalidation — the merchant's storefront
    # go-live gate must see the activated plan without waiting out the
    # 60s cache TTL.
    from src.application.services.wallet_service import WalletService

    await WalletService(db).invalidate_cache(result.intent.tenant_id)

    return SuccessResponse(
        data={
            "proof_id": str(result.proof.id),
            "status": result.proof.status,
            "activated": bool(result.activation and result.activation.activated),
            "plan": result.intent.plan_key,
            "billing_cycle": result.intent.billing_cycle,
        },
        message="Proof approved — subscription activated",
    )


@router.post("/{proof_id}/reject", response_model=SuccessResponse[dict])
async def reject_subscription_proof(
    proof_id: UUID,
    request: RejectProofRequest,
    admin_user_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await ReviewSubscriptionPaymentProofUseCase(db).reject(
        proof_id=proof_id,
        admin_user_id=admin_user_id,
        reason=request.reason,
    )
    await db.commit()
    return SuccessResponse(
        data={"proof_id": str(result.proof.id), "status": result.proof.status},
        message="Proof rejected",
    )
