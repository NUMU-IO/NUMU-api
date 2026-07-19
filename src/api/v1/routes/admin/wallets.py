"""Admin wallet management (pay-as-you-go tier).

URL: /api/v1/admin/wallets — requires admin auth.

* ``GET  /``                         — list wallets (balance, tenant, status)
* ``GET  /{tenant_id}``              — one wallet + recent ledger
* ``POST /{tenant_id}/adjust``       — manual credit/debit. The ledger row
  (kind=adjustment, actor_user_id, note) IS the audit trail; balance is
  never edited directly.
* ``PATCH /{tenant_id}/config``      — status / commission override /
  negative allowance.
* ``GET  /topup-proofs``             — InstaPay top-up review queue.
* ``POST /topup-proofs/{proof_id}/approve|reject`` — resolve a proof;
  approve credits idempotently (``proof:{proof_id}``).
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.dependencies.services import get_storage_service
from src.api.responses import SuccessResponse
from src.application.services.wallet_service import (
    WalletService,
    WalletSuspendedError,
)
from src.application.use_cases.wallet.credit_wallet import notify_topup_credited
from src.application.use_cases.wallet.review_topup_proof import (
    ReviewTopupProofUseCase,
)
from src.core.entities.wallet import WalletStatus, WalletTransactionKind
from src.core.interfaces.services.storage_service import IStorageService
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.wallet import (
    MerchantWalletModel,
    WalletTopupIntentModel,
    WalletTopupProofModel,
    WalletTransactionModel,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class AdjustRequest(BaseModel):
    amount_cents: int = Field(description="Signed: positive credits, negative debits.")
    note: str = Field(min_length=3, max_length=500)


class WalletConfigRequest(BaseModel):
    status: WalletStatus | None = None
    commission_bps_override: int | None = Field(default=None, ge=0, le=10_000)
    clear_commission_override: bool = False
    negative_allowance_cents: int | None = Field(default=None, ge=0)


class RejectProofRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _wallet_dict(w: MerchantWalletModel) -> dict:
    return {
        "id": str(w.id),
        "tenant_id": str(w.tenant_id),
        "balance_cents": w.balance_cents,
        "pending_balance_cents": w.pending_balance_cents,
        "currency": w.currency,
        "status": w.status,
        "commission_bps_override": w.commission_bps_override,
        "negative_allowance_cents": w.negative_allowance_cents,
        "last_warning_level": w.last_warning_level,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


@router.get("", response_model=SuccessResponse[list[dict]])
async def list_wallets(
    _admin: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 50,
):
    rows = (
        await db.execute(
            select(MerchantWalletModel, TenantModel.name)
            .join(TenantModel, TenantModel.id == MerchantWalletModel.tenant_id)
            .order_by(MerchantWalletModel.balance_cents.asc())
            .offset(max(skip, 0))
            .limit(min(max(limit, 1), 200))
        )
    ).all()
    return SuccessResponse(
        data=[{**_wallet_dict(w), "tenant_name": name} for w, name in rows]
    )


# Static paths must be declared before /{tenant_id} — repo convention.


class WalletSettingsPatch(BaseModel):
    """Partial update; ``None`` clears the override back to env default."""

    topups_enabled: bool | None = None
    checkout_gate_enabled: bool | None = None
    card_enabled: bool | None = None
    vodafone_cash_enabled: bool | None = None
    instapay_enabled: bool | None = None
    commission_bps_default: int | None = Field(default=None, ge=0, le=10_000)
    negative_allowance_cents: int | None = Field(default=None, ge=0)
    low_balance_threshold_cents: int | None = Field(default=None, ge=0)
    vodafone_cash_number: str | None = Field(default=None, max_length=20)
    instapay_ipa: str | None = Field(default=None, max_length=80)
    instapay_display_name: str | None = Field(default=None, max_length=80)


@router.get("/settings", response_model=SuccessResponse[dict])
async def get_wallet_admin_settings(
    _admin: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Effective wallet configuration (env defaults + admin overrides)."""
    from src.application.services.wallet_settings import (
        get_wallet_settings,
        wallet_settings_to_dict,
    )

    admin_settings = await get_wallet_settings(db, use_cache=False)
    return SuccessResponse(data=wallet_settings_to_dict(admin_settings))


@router.put("/settings", response_model=SuccessResponse[dict])
async def update_wallet_admin_settings(
    request: WalletSettingsPatch,
    _admin: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Persist admin overrides for wallet behaviour (platform_config).

    Controls the top-up methods offered to merchants, the default
    commission rate for commission-bearing plans, thresholds, and the
    platform's Vodafone Cash number / InstaPay IPA — no deploy needed.
    """
    from src.application.services.wallet_settings import (
        update_wallet_settings,
        wallet_settings_to_dict,
    )

    # exclude_unset so "field absent" (keep) differs from "field: null" (clear).
    patch = request.model_dump(exclude_unset=True)
    merged = await update_wallet_settings(db, patch)
    await db.commit()
    logger.info("admin_wallet_settings_updated", extra={"fields": list(patch)})
    return SuccessResponse(
        data=wallet_settings_to_dict(merged), message="Wallet settings updated"
    )


@router.get("/topup-proofs", response_model=SuccessResponse[dict])
async def list_topup_proofs(
    _admin: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[IStorageService, Depends(get_storage_service)],
    status: str = "awaiting_review",
    skip: int = 0,
    limit: int = 50,
):
    rows = (
        await db.execute(
            select(WalletTopupProofModel, WalletTopupIntentModel, TenantModel.name)
            .join(
                WalletTopupIntentModel,
                WalletTopupIntentModel.id == WalletTopupProofModel.topup_intent_id,
            )
            .join(TenantModel, TenantModel.id == WalletTopupProofModel.tenant_id)
            .where(WalletTopupProofModel.status == status)
            .order_by(WalletTopupProofModel.created_at.asc())
            .offset(max(skip, 0))
            .limit(min(max(limit, 1), 200))
        )
    ).all()

    # Global per-status counts so the queue can label its tabs (mirrors
    # the WhatsApp access-request queue shape).
    count_rows = (
        await db.execute(
            select(
                WalletTopupProofModel.status,
                func.count(WalletTopupProofModel.id),
            ).group_by(WalletTopupProofModel.status)
        )
    ).all()
    counts = {status_val: int(n) for status_val, n in count_rows}

    async def _signed(key: str) -> str | None:
        try:
            return await storage.get_signed_url(key, expires_in=3600)
        except Exception:  # noqa: BLE001 — a broken URL must not kill the queue
            return None

    proofs = []
    for p, i, tenant_name in rows:
        proofs.append({
            "proof_id": str(p.id),
            "tenant_id": str(p.tenant_id),
            "tenant_name": tenant_name,
            "topup_id": str(i.id),
            "method": i.method,
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
            "image_url": await _signed(p.proof_image_key),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return SuccessResponse(data={"proofs": proofs, "counts": counts})


@router.post("/topup-proofs/{proof_id}/approve", response_model=SuccessResponse[dict])
async def approve_topup_proof(
    proof_id: UUID,
    admin_user_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await ReviewTopupProofUseCase(db).approve(
        proof_id=proof_id, admin_user_id=admin_user_id
    )
    await db.commit()
    if result.credited_balance_cents is not None:
        await notify_topup_credited(
            db,
            tenant_id=result.intent.tenant_id,
            amount_cents=result.intent.amount_cents,
            balance_after_cents=result.credited_balance_cents,
        )
    return SuccessResponse(
        data={
            "proof_id": str(result.proof.id),
            "status": result.proof.status,
            "credited_balance_cents": result.credited_balance_cents,
        },
        message="Proof approved and wallet credited",
    )


@router.post("/topup-proofs/{proof_id}/reject", response_model=SuccessResponse[dict])
async def reject_topup_proof(
    proof_id: UUID,
    request: RejectProofRequest,
    admin_user_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await ReviewTopupProofUseCase(db).reject(
        proof_id=proof_id,
        admin_user_id=admin_user_id,
        reason=request.reason,
    )
    await db.commit()
    return SuccessResponse(
        data={"proof_id": str(result.proof.id), "status": result.proof.status},
        message="Proof rejected",
    )


@router.get("/{tenant_id}", response_model=SuccessResponse[dict])
async def get_wallet_admin(
    tenant_id: UUID,
    _admin: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    wallet = (
        await db.execute(
            select(MerchantWalletModel).where(
                MerchantWalletModel.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if wallet is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    ledger = (
        (
            await db.execute(
                select(WalletTransactionModel)
                .where(WalletTransactionModel.wallet_id == wallet.id)
                .order_by(WalletTransactionModel.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return SuccessResponse(
        data={
            **_wallet_dict(wallet),
            "ledger": [
                {
                    "id": str(t.id),
                    "kind": t.kind,
                    "amount_cents": t.amount_cents,
                    "balance_after_cents": t.balance_after_cents,
                    "order_id": str(t.order_id) if t.order_id else None,
                    "idempotency_key": t.idempotency_key,
                    "actor_user_id": str(t.actor_user_id) if t.actor_user_id else None,
                    "note": t.note,
                    "meta": t.meta,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in ledger
            ],
        }
    )


@router.post("/{tenant_id}/adjust", response_model=SuccessResponse[dict])
async def adjust_wallet(
    tenant_id: UUID,
    request: AdjustRequest,
    admin_user_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if request.amount_cents == 0:
        raise HTTPException(status_code=422, detail="Amount cannot be zero")

    service = WalletService(db)
    try:
        tx = await service.apply_entry(
            tenant_id=tenant_id,
            kind=WalletTransactionKind.ADJUSTMENT,
            amount_cents=request.amount_cents,
            actor_user_id=admin_user_id,
            note=request.note,
            meta={"source": "admin_adjust"},
        )
    except WalletSuspendedError:
        raise HTTPException(
            status_code=409,
            detail="Wallet is suspended — unsuspend before adjusting.",
        )
    wallet = await service.get_or_create_wallet(tenant_id)
    service.bump_warning_level(wallet)
    await db.commit()
    await service.invalidate_cache(tenant_id)

    logger.info(
        "admin_wallet_adjustment",
        extra={
            "tenant_id": str(tenant_id),
            "amount_cents": request.amount_cents,
        },
    )
    return SuccessResponse(
        data={
            "transaction_id": str(tx.id) if tx else None,
            "balance_after_cents": tx.balance_after_cents if tx else None,
        },
        message="Adjustment applied",
    )


@router.patch("/{tenant_id}/config", response_model=SuccessResponse[dict])
async def update_wallet_config(
    tenant_id: UUID,
    request: WalletConfigRequest,
    _admin: Annotated[object, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    service = WalletService(db)
    wallet = await service.get_or_create_wallet(tenant_id)
    if request.status is not None:
        wallet.status = request.status.value
    if request.clear_commission_override:
        wallet.commission_bps_override = None
    elif request.commission_bps_override is not None:
        wallet.commission_bps_override = request.commission_bps_override
    if request.negative_allowance_cents is not None:
        wallet.negative_allowance_cents = request.negative_allowance_cents
    await db.commit()
    await service.invalidate_cache(tenant_id)
    return SuccessResponse(data=_wallet_dict(wallet), message="Wallet updated")
