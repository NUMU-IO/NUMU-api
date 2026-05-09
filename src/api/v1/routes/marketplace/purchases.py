"""Paid-theme purchase + refund routes.

Auth model:
  * `/checkout-session`, `/list`, `/refund` — JWT-authenticated; the
    user_id off the JWT identifies the buyer.
  * `/webhooks/stripe` — NO JWT. Authenticated via Stripe signature
    verification. The endpoint reads the raw body to verify the HMAC,
    so we can't use a Pydantic model param (FastAPI consumes the body
    once on parsing). We pass the raw bytes to the service.

Pricing convention:
  * Theme prices are stored in `price_cents` with `currency` (ISO).
  * Stripe expects `amount` in the smallest unit (cents/piastres) which
    matches our storage 1:1.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from src.api.dependencies import get_current_user_id
from src.api.dependencies.repositories import get_marketplace_repository
from src.api.dependencies.services import get_payment_service
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.marketplace import (
    CreateCheckoutSessionRequest,
    CreateCheckoutSessionResponse,
    PurchaseListResponse,
    PurchaseOut,
    RefundPurchaseRequest,
)
from src.application.services.theme_purchase_service import ThemePurchaseService
from src.config import settings
from src.core.interfaces.services.payment_service import IPaymentService
from src.infrastructure.external_services.stripe import StripePaymentService
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)

router = APIRouter(
    prefix="/marketplace",
    tags=["Marketplace Purchases"],
)


def _svc(
    repo: Annotated[MarketplaceRepository, Depends(get_marketplace_repository)],
    payment: Annotated[IPaymentService, Depends(get_payment_service)],
) -> ThemePurchaseService:
    return ThemePurchaseService(
        marketplace_repo=repo,
        payment_service=payment,
    )


@router.post(
    "/themes/{theme_id}/checkout-session",
    response_model=SuccessResponse[CreateCheckoutSessionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_checkout_session(
    theme_id: UUID,
    body: CreateCheckoutSessionRequest,
    svc: Annotated[ThemePurchaseService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Start a Stripe checkout for a paid marketplace theme.

    Returns the payment-intent's `client_secret` so the buyer's browser
    can confirm the charge directly via Stripe.js — we never see card
    details. The pending purchase row is promoted to `succeeded` by
    the webhook handler when Stripe confirms payment.
    """
    if str(theme_id) != body.marketplace_theme_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="theme_id in path does not match body",
        )
    try:
        data = await svc.create_checkout_session(
            user_id=user_id,
            marketplace_theme_id=theme_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=CreateCheckoutSessionResponse(**data))


@router.get(
    "/purchases",
    response_model=SuccessResponse[PurchaseListResponse],
)
async def list_purchases(
    svc: Annotated[ThemePurchaseService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """List the authenticated user's marketplace purchases."""
    purchases = await svc.list_user_purchases(user_id)
    items = [
        PurchaseOut(
            id=str(p.id),
            marketplace_theme_id=str(p.marketplace_theme_id),
            amount_cents=p.amount_cents,
            currency=p.currency,
            status=p.status.value,
            refunded_amount_cents=p.refunded_amount_cents,
            created_at=p.created_at.isoformat() if p.created_at else "",
            theme_name=p.purchase_metadata.get("theme_slug")
            if p.purchase_metadata
            else None,
        )
        for p in purchases
    ]
    return SuccessResponse(data=PurchaseListResponse(purchases=items))


@router.post("/purchases/{purchase_id}/refund")
async def refund_purchase(
    purchase_id: UUID,
    body: RefundPurchaseRequest,
    svc: Annotated[ThemePurchaseService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Refund a marketplace purchase.

    Authorization: only the original buyer (or an admin — TODO when the
    admin role wiring lands here) can refund. Existing installs of the
    refunded theme are left intact (best customer experience); future
    install/activate calls for the same theme are blocked.
    """
    # Buyer-scoped check: load the purchase and confirm ownership before
    # we ever talk to Stripe. Avoids a refund-then-reject race and stops
    # information disclosure (a non-owner sees only "purchase not found").
    repo: MarketplaceRepository = svc._marketplace  # type: ignore[attr-defined]
    purchase = await repo.get_purchase_by_id(purchase_id)
    if purchase is None or purchase.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Purchase not found"
        )

    try:
        updated = await svc.refund_purchase(
            purchase_id=purchase_id,
            amount_cents=body.amount_cents,
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return SuccessResponse(
        data={
            "purchase_id": str(updated.id),
            "status": updated.status.value,
            "refunded_amount_cents": updated.refunded_amount_cents,
            "amount_cents": updated.amount_cents,
        },
        message="Refund processed",
    )


@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    svc: Annotated[ThemePurchaseService, Depends(_svc)],
    stripe_signature: Annotated[str | None, Header(alias="stripe-signature")] = None,
):
    """Stripe payment-event webhook.

    Stripe replays events on transient errors, so this endpoint MUST be
    idempotent — duplicate events for the same payment intent are safe
    because we key the purchase row on `stripe_payment_intent_id` and
    only ever update fields based on the latest event state.

    Signature verification is mandatory: an unverified call could let
    an attacker mark arbitrary purchases as `succeeded` and unlock paid
    themes for free.
    """
    if not settings.stripe_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook secret not configured",
        )
    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )

    raw_body = await request.body()

    # Verify via the existing StripePaymentService — it already wraps
    # `stripe.Webhook.construct_event` with the configured secret.
    stripe_svc = StripePaymentService()
    event = stripe_svc.verify_webhook_signature(raw_body, stripe_signature)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe webhook signature",
        )

    await svc.handle_stripe_event(event)
    return SuccessResponse(data={"received": True})
