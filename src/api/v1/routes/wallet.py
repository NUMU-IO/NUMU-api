"""Merchant wallet routes — balance, ledger, top-ups (pay-as-you-go tier).

GET  /api/v1/wallet
GET  /api/v1/wallet/transactions
POST /api/v1/wallet/topups
GET  /api/v1/wallet/topups/{topup_id}
POST /api/v1/wallet/topups/{topup_id}/proof

Tenant-level like /billing (resolved from the authenticated owner), NOT
store-scoped: the wallet is shared across the tenant's stores. Static
paths are declared before ``/wallet/topups/{topup_id}`` (repo convention:
static-above-dynamic).
"""

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.services import get_storage_service
from src.api.dependencies.tenant_context import resolve_owner_tenant
from src.api.responses import SuccessResponse
from src.api.utils.upload_validation import validate_image_upload
from src.application.services.wallet_service import WalletService
from src.application.use_cases.wallet.create_topup import CreateTopupUseCase
from src.application.use_cases.wallet.credit_wallet import notify_topup_credited
from src.application.use_cases.wallet.submit_topup_proof import (
    SubmitTopupProofUseCase,
)
from src.config.settings import get_settings
from src.core.entities.wallet import (
    MANUAL_TOPUP_METHODS,
    TopupMethod,
    WalletStatus,
)
from src.core.interfaces.services.storage_service import IStorageService
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.wallet import (
    WalletTopupIntentModel,
    WalletTransactionModel,
)
from src.infrastructure.external_services.image.proof_sanitizer import (
    ProofImageDecodeError,
    sanitize_proof_image,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────


class WalletResponse(BaseModel):
    balance_cents: int
    # Manual top-ups awaiting verification — shown as "on hold", not
    # spendable (gate/commissions read balance_cents only).
    pending_balance_cents: int
    currency: str
    status: str
    effective_commission_bps: int
    negative_allowance_cents: int
    low_balance_threshold_cents: int
    is_blocked: bool
    low_balance_level: int  # 0 healthy, 1 low, 2 negative, 3 blocked
    # Admin-controlled: which top-up methods the dialog should offer.
    # Only methods that are BOTH enabled and platform-configured (VC
    # number / InstaPay IPA / Kashier creds present) appear as true.
    methods_enabled: dict[str, bool]
    topups_enabled: bool
    min_topup_cents: int


class WalletTransactionResponse(BaseModel):
    id: str
    kind: str
    amount_cents: int
    balance_after_cents: int
    currency: str
    order_id: str | None
    note: str | None
    created_at: datetime


class CreateTopupRequest(BaseModel):
    method: TopupMethod
    amount_cents: int = Field(gt=0)


class TopupResponse(BaseModel):
    id: str
    method: str
    amount_cents: int
    currency: str
    status: str
    special_reference: str
    checkout_url: str | None = None
    # Manual-method payload (InstaPay / Vodafone Cash): destination,
    # reference, QR (InstaPay only), expiry.
    manual: dict | None = None
    expires_at: datetime | None = None


# ─── Helpers ──────────────────────────────────────────────────────────────


async def _resolve_tenant(
    http_request: Request, db: AsyncSession, user_id: UUID
) -> TenantModel:
    """Current-store tenant first (X-Tenant-Id via middleware), then the
    owner's most relevant tenant. One owner can have MULTIPLE tenants
    (one per store), so a bare owner_id scalar_one_or_none would raise
    MultipleResultsFound for multi-store merchants."""
    return await resolve_owner_tenant(http_request, db, user_id)


def _topup_response(
    intent: WalletTopupIntentModel,
    *,
    checkout_url: str | None = None,
    manual: dict | None = None,
) -> TopupResponse:
    if manual is None and intent.method in MANUAL_TOPUP_METHODS:
        manual = {
            "method": intent.method,
            "reference_code": intent.special_reference,
            "destination": intent.display_destination,
            "destination_label": intent.display_destination,
            "qr_payload": intent.qr_payload,
            "expires_at": intent.expires_at.isoformat() if intent.expires_at else None,
        }
    return TopupResponse(
        id=str(intent.id),
        method=intent.method,
        amount_cents=intent.amount_cents,
        currency=intent.currency,
        status=intent.status,
        special_reference=intent.special_reference,
        # For card intents the stored gateway_payload IS the hosted
        # session URL — return it on polling too so a refresh can resume.
        checkout_url=checkout_url
        or (
            intent.gateway_payload if intent.method == TopupMethod.CARD.value else None
        ),
        manual=manual,
        expires_at=intent.expires_at,
    )


# ─── Routes (static above dynamic) ────────────────────────────────────────


@router.get(
    "/wallet",
    response_model=SuccessResponse[WalletResponse],
    summary="Get the merchant wallet",
    operation_id="get_wallet",
)
async def get_wallet(
    http_request: Request,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant = await _resolve_tenant(http_request, db, user_id)
    service = WalletService(db)
    admin = await service.load_admin_defaults()
    wallet = await service.get_or_create_wallet(tenant.id)
    await db.commit()

    allowance = service.allowance_for(wallet)
    level = service.current_warning_level(wallet)
    bps = await service.effective_commission_bps_admin(tenant, wallet)
    is_blocked = (
        bps > 0
        and wallet.status == WalletStatus.ACTIVE.value
        and wallet.balance_cents < -allowance
    )
    return SuccessResponse(
        data=WalletResponse(
            balance_cents=wallet.balance_cents,
            pending_balance_cents=wallet.pending_balance_cents,
            currency=wallet.currency,
            status=wallet.status,
            effective_commission_bps=bps,
            negative_allowance_cents=allowance,
            low_balance_threshold_cents=service._low_threshold,
            is_blocked=is_blocked,
            low_balance_level=level,
            methods_enabled=admin.effective_methods_map(),
            topups_enabled=admin.topups_enabled or get_settings().ff_wallet_topups,
            min_topup_cents=admin.min_topup_cents,
        )
    )


@router.get(
    "/wallet/transactions",
    response_model=SuccessResponse[list[WalletTransactionResponse]],
    summary="List wallet ledger entries (newest first)",
    operation_id="list_wallet_transactions",
)
async def list_wallet_transactions(
    http_request: Request,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 50,
):
    tenant = await _resolve_tenant(http_request, db, user_id)
    limit = min(max(limit, 1), 100)
    rows = (
        (
            await db.execute(
                select(WalletTransactionModel)
                .where(WalletTransactionModel.tenant_id == tenant.id)
                .order_by(WalletTransactionModel.created_at.desc())
                .offset(max(skip, 0))
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return SuccessResponse(
        data=[
            WalletTransactionResponse(
                id=str(t.id),
                kind=t.kind,
                amount_cents=t.amount_cents,
                balance_after_cents=t.balance_after_cents,
                currency=t.currency,
                order_id=str(t.order_id) if t.order_id else None,
                note=t.note,
                created_at=t.created_at,
            )
            for t in rows
        ]
    )


class TopupHistoryItem(BaseModel):
    id: str
    method: str
    amount_cents: int
    currency: str
    # pending | awaiting_proof | under_review | succeeded | rejected | expired
    status: str
    special_reference: str
    created_at: datetime
    # Set when the latest receipt for this top-up was rejected — the
    # merchant-visible "why" (the wallet ledger only shows money that
    # actually moved, so without this a rejection is invisible).
    rejection_reason: str | None = None


@router.get(
    "/wallet/topups",
    response_model=SuccessResponse[list[TopupHistoryItem]],
    summary="List the merchant's top-ups (newest first, all statuses)",
    operation_id="list_wallet_topups",
)
async def list_wallet_topups(
    http_request: Request,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 20,
):
    tenant = await _resolve_tenant(http_request, db, user_id)
    intents = (
        (
            await db.execute(
                select(WalletTopupIntentModel)
                .where(WalletTopupIntentModel.tenant_id == tenant.id)
                .order_by(WalletTopupIntentModel.created_at.desc())
                .limit(min(max(limit, 1), 50))
            )
        )
        .scalars()
        .all()
    )

    return SuccessResponse(
        data=[
            TopupHistoryItem(
                id=str(i.id),
                method=i.method,
                amount_cents=i.amount_cents,
                currency=i.currency,
                status=i.status,
                special_reference=i.special_reference,
                created_at=i.created_at,
                # Set by the admin reject flow (review_topup_proof) —
                # survives on the intent so the merchant sees the "why"
                # even after the intent reopens for a new receipt.
                rejection_reason=i.failure_reason,
            )
            for i in intents
        ]
    )


@router.post(
    "/wallet/topups",
    response_model=SuccessResponse[TopupResponse],
    status_code=201,
    summary="Create a wallet top-up",
    operation_id="create_wallet_topup",
)
async def create_wallet_topup(
    http_request: Request,
    request: CreateTopupRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant = await _resolve_tenant(http_request, db, user_id)

    # Rate-bound open intents so a stuck client can't mint hundreds.
    open_count = (
        await db.execute(
            select(func.count(WalletTopupIntentModel.id)).where(
                WalletTopupIntentModel.tenant_id == tenant.id,
                WalletTopupIntentModel.status.in_((
                    "pending",
                    "awaiting_proof",
                    "under_review",
                )),
            )
        )
    ).scalar_one()
    if open_count >= 10:
        raise HTTPException(
            status_code=429,
            detail="Too many open top-ups. Complete or wait for them to expire.",
        )

    result = await CreateTopupUseCase(db).execute(
        tenant_id=tenant.id,
        user_id=user_id,
        method=request.method,
        amount_cents=request.amount_cents,
    )
    await db.commit()
    return SuccessResponse(
        data=_topup_response(
            result.intent,
            checkout_url=result.checkout_url,
            manual=result.manual,
        ),
        message="Top-up created",
    )


@router.get(
    "/wallet/topups/{topup_id}",
    response_model=SuccessResponse[TopupResponse],
    summary="Poll a top-up's status",
    operation_id="get_wallet_topup",
)
async def get_wallet_topup(
    http_request: Request,
    topup_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant = await _resolve_tenant(http_request, db, user_id)
    intent = (
        await db.execute(
            select(WalletTopupIntentModel).where(
                WalletTopupIntentModel.id == topup_id,
                WalletTopupIntentModel.tenant_id == tenant.id,
            )
        )
    ).scalar_one_or_none()
    if intent is None:
        raise HTTPException(status_code=404, detail="Top-up not found")
    return SuccessResponse(data=_topup_response(intent))


@router.post(
    "/wallet/topups/{topup_id}/proof",
    response_model=SuccessResponse[dict],
    status_code=201,
    summary="Upload a transfer receipt for a manual top-up",
    operation_id="submit_wallet_topup_proof",
)
async def submit_wallet_topup_proof(
    http_request: Request,
    topup_id: UUID,
    transaction_ref: Annotated[str, Form(min_length=3, max_length=64)],
    file: Annotated[UploadFile, File(description="Transfer receipt")],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_service: Annotated[IStorageService, Depends(get_storage_service)],
    declared_amount_cents: Annotated[int | None, Form()] = None,
):
    tenant = await _resolve_tenant(http_request, db, user_id)

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

    use_case = SubmitTopupProofUseCase(session=db, storage_service=storage_service)
    result = await use_case.execute(
        tenant_id=tenant.id,
        topup_intent_id=topup_id,
        image_bytes=sanitized.bytes,
        image_content_type=sanitized.content_type,
        transaction_ref=transaction_ref,
        declared_amount_cents=declared_amount_cents,
        image_perceptual_hash=sanitized.perceptual_hash,
    )
    await db.commit()

    if result.credited_balance_cents is not None:
        await notify_topup_credited(
            db,
            tenant_id=tenant.id,
            amount_cents=result.intent.amount_cents,
            balance_after_cents=result.credited_balance_cents,
        )

    if result.on_hold:
        # The hold is committed — refresh the hub's cached wallet view.
        await WalletService(db).invalidate_cache(tenant.id)

    return SuccessResponse(
        data={
            "proof_id": str(result.proof.id),
            "topup_id": str(result.intent.id),
            "status": result.proof.status,
            "topup_status": result.intent.status,
            "reasons": result.decision.reasons,
            "credited_balance_cents": result.credited_balance_cents,
            "on_hold": result.on_hold,
            "pending_balance_cents": (
                result.intent.amount_cents if result.on_hold else 0
            ),
        },
        message=(
            "Top-up credited"
            if result.credited_balance_cents is not None
            else "Credit added on hold — verification in progress"
        ),
    )
