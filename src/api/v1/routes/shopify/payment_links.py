"""Payment link endpoints for COD-to-Prepaid conversion.

Provides three endpoints:
- ``POST /{store_id}/payment-links``    — create a payment session (internal auth)
- ``GET /payment-links/{session_id}``   — serve payment page data (public, no auth)
- ``POST /payment-links/{session_id}/complete`` — mark payment complete (public, no auth)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status

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
_PAY_BASE_URL = "https://pay.numu.app"


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

    payment_url = f"{_PAY_BASE_URL}/{model.id}"

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
    summary="Mark a payment session as completed (called by Paymob callback)",
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
):
    model = await repo.get_by_id(session_id)
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
        gateway_used=body.gateway_used,
        gateway_transaction_id=body.gateway_transaction_id,
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
            f"COD order converted to prepaid via {body.gateway_used}. "
            f"Transaction: {body.gateway_transaction_id}",
        )

    return SuccessResponse(
        data=CompletePaymentResponse(
            status="completed",
            message="Payment recorded successfully",
        ),
    )
