"""Payment link endpoints for COD-to-Prepaid conversion.

Provides three endpoints:
- ``POST /{store_id}/payment-links``    — create a payment session (internal auth)
- ``GET /payment-links/{session_id}``   — serve payment page data (public, no auth)
- ``POST /payment-links/{session_id}/complete`` — mark payment complete (public, no auth)
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.shopify import (
    get_payment_link_session_repo,
    get_shopify_installation_repo,
    get_shopify_settings_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import (
    CompletePaymentRequest,
    CompletePaymentResponse,
    CreatePaymentLinkRequest,
    PaymentLinkPublicResponse,
    PaymentLinkResponse,
)
from src.config.settings import get_settings
from src.infrastructure.external_services.shopify.admin_client import (
    add_tags,
    append_note,
    order_gid,
)
from src.infrastructure.repositories.shopify_repository import (
    PaymentLinkSessionRepository,
    ShopifyAppSettingsRepository,
    ShopifyInstallationRepository,
)

# Two routers: one authenticated (store-scoped), one public (session-scoped)
router_internal = APIRouter(dependencies=[Depends(verify_internal_key)])
router_public = APIRouter()

_DEFAULT_EXPIRY_HOURS = 24


@router_internal.post(
    "/{store_id}/payment-links",
    response_model=SuccessResponse[PaymentLinkResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment link session for COD-to-Prepaid conversion",
    operation_id="shopify_create_payment_link",
)
async def create_payment_link(
    store_id: Annotated[UUID, Path()],
    body: CreatePaymentLinkRequest,
    repo: Annotated[
        PaymentLinkSessionRepository, Depends(get_payment_link_session_repo)
    ],
    settings_repo: Annotated[
        ShopifyAppSettingsRepository, Depends(get_shopify_settings_repo)
    ],
):
    settings = await settings_repo.get_or_create(store_id)

    # Determine available gateways
    gateways = []
    if settings.paymob_connected:
        gateways.append("paymob")
    if not gateways:
        gateways.append("paymob")  # Default to paymob for MVP

    model = await repo.create(
        store_id=store_id,
        shopify_order_id=body.shopify_order_id,
        amount_cents=body.amount_cents,
        currency=body.currency,
        available_gateways=gateways,
        merchant_branding=None,
        expires_at=datetime.now(UTC) + timedelta(hours=_DEFAULT_EXPIRY_HOURS),
    )

    from src.application.services.shopify_nudge_service import payment_page_url

    payment_url = payment_page_url(model.id)

    return SuccessResponse(
        data=PaymentLinkResponse(
            id=str(model.id),
            store_id=str(model.store_id),
            shopify_order_id=model.shopify_order_id,
            amount_cents=model.amount_cents,
            currency=model.currency,
            status=model.status,
            available_gateways=gateways,
            merchant_branding=model.merchant_branding,
            payment_url=payment_url,
            expires_at=model.expires_at,
            created_at=model.created_at,
        ),
        message="Payment link created",
    )


@router_public.get(
    "/payment-links/{session_id}",
    response_model=SuccessResponse[PaymentLinkPublicResponse],
    summary="Get payment session data for the payment page",
    operation_id="get_payment_link_session",
)
async def get_payment_link(
    session_id: Annotated[UUID, Path()],
    repo: Annotated[
        PaymentLinkSessionRepository, Depends(get_payment_link_session_repo)
    ],
):
    model = await repo.get_by_id(session_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment session not found",
        )

    now = datetime.now(UTC)
    is_expired = model.expires_at.tzinfo and now > model.expires_at or False
    if model.expires_at and not model.expires_at.tzinfo:
        is_expired = now > model.expires_at.replace(tzinfo=UTC)

    effective_status = (
        "expired" if is_expired and model.status == "pending" else model.status
    )

    branding = model.merchant_branding or {}

    return SuccessResponse(
        data=PaymentLinkPublicResponse(
            session_id=str(model.id),
            amount_cents=model.amount_cents,
            currency=model.currency,
            status=effective_status,
            available_gateways=model.available_gateways or [],
            merchant_branding=model.merchant_branding,
            store_name=branding.get("store_name", ""),
            order_number=model.shopify_order_id or "",
            expires_at=model.expires_at,
            is_expired=is_expired,
        ),
    )


@router_public.post(
    "/payment-links/{session_id}/complete",
    response_model=SuccessResponse[CompletePaymentResponse],
    summary="Mark a payment session as completed (verified callers only)",
    operation_id="complete_payment_link",
)
async def complete_payment_link(
    session_id: Annotated[UUID, Path()],
    body: CompletePaymentRequest,
    repo: Annotated[
        PaymentLinkSessionRepository, Depends(get_payment_link_session_repo)
    ],
    install_repo: Annotated[
        ShopifyInstallationRepository, Depends(get_shopify_installation_repo)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    x_internal_key: Annotated[str | None, Header(alias="X-Internal-Key")] = None,
):
    """Completion needs proof — a session UUID alone must never mark a COD
    order as paid. Two accepted callers:

    1. The Shopify app's server (``X-Internal-Key``) after its own
       verified flow.
    2. A Paymob transaction-processed callback: body carries
       ``paymob_payload`` + ``paymob_hmac``, verified against the
       merchant's stored hmac_secret with an amount cross-check.
    """
    expected_key = get_settings().shopify_internal_key
    internal_ok = bool(
        x_internal_key
        and expected_key
        and hmac.compare_digest(x_internal_key, expected_key)
    )

    model = await repo.get_by_id(session_id)

    gateway_used = body.gateway_used
    gateway_transaction_id = body.gateway_transaction_id

    if not internal_ok:
        # Unauthenticated: require a verifiable Paymob proof. A missing
        # session is also a 401 here so unauthenticated callers can't
        # probe which session UUIDs exist.
        if not (body.paymob_payload and body.paymob_hmac and model):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Completion requires the internal key or a signed "
                    "Paymob callback payload"
                ),
            )

        from src.application.services.shopify_paymob_verification import (
            REASON_AMOUNT_MISMATCH,
            verify_paymob_completion,
        )

        accepted, reason = await verify_paymob_completion(
            db,
            store_id=model.store_id,
            expected_amount_cents=model.amount_cents,
            paymob_payload=body.paymob_payload,
            paymob_hmac=body.paymob_hmac,
        )
        if not accepted:
            raise HTTPException(
                status_code=(
                    status.HTTP_400_BAD_REQUEST
                    if reason == REASON_AMOUNT_MISMATCH
                    else status.HTTP_401_UNAUTHORIZED
                ),
                detail=f"Paymob verification failed: {reason}",
            )
        # Trust only the verified payload for the transaction identity.
        gateway_used = "paymob"
        obj = body.paymob_payload.get("obj") or {}
        gateway_transaction_id = str(obj.get("id") or body.gateway_transaction_id)

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment session not found",
        )

    if model.status == "completed":
        return SuccessResponse(
            data=CompletePaymentResponse(
                status="completed", message="Already completed"
            ),
        )

    # Check expiry
    now = datetime.now(UTC)
    expires = model.expires_at
    if expires and not expires.tzinfo:
        expires = expires.replace(tzinfo=UTC)
    if expires and now > expires:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Payment session has expired",
        )

    # Mark completed
    updated = await repo.mark_completed(
        session_id,
        gateway_used=gateway_used,
        gateway_transaction_id=gateway_transaction_id,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update session",
        )

    # Update Shopify order: add "paid via numu" tag, append note
    installation = await install_repo.get_by_store_id(model.store_id)
    if installation:
        gid = order_gid(model.shopify_order_id or "")
        await add_tags(
            installation.shopify_domain,
            installation.access_token_encrypted,
            gid,
            ["numu-paid", "numu-cod-converted"],
        )
        await append_note(
            installation.shopify_domain,
            installation.access_token_encrypted,
            gid,
            f"COD order converted to prepaid via {gateway_used}. "
            f"Transaction: {gateway_transaction_id}",
        )

    return SuccessResponse(
        data=CompletePaymentResponse(
            status="completed",
            message="Payment recorded successfully",
        ),
    )
