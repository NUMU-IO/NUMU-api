"""Abandoned-checkout management routes nested under stores.

URL: /stores/{store_id}/abandoned-checkouts

Backs the merchant hub's Abandoned Checkouts page. The data itself comes
from a separate `abandoned_checkouts` table — populated by the storefront
checkout flow + a background job that flips `abandoned_at` once a row
sits inactive past the threshold. Both writers live outside this router;
here we only expose merchant-facing read + recovery actions.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies import (
    get_abandoned_checkout_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.abandoned_checkout import (
    AbandonedCheckoutListResponse,
    AbandonedCheckoutResponse,
    SendRecoveryEmailResponse,
)
from src.core.entities.abandoned_checkout import AbandonedCheckout
from src.core.entities.store import Store
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories import AbandonedCheckoutRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/abandoned-checkouts")


def _to_response(c: AbandonedCheckout) -> AbandonedCheckoutResponse:
    return AbandonedCheckoutResponse(
        id=c.id,
        store_id=c.store_id,
        customer_id=c.customer_id,
        email=c.email,
        phone=c.phone,
        line_items=c.line_items,  # validated by AbandonedCheckoutLineItem
        shipping_address=c.shipping_address,
        subtotal=c.subtotal,
        shipping_cost=c.shipping_cost,
        tax_amount=c.tax_amount,
        discount_amount=c.discount_amount,
        total=c.total,
        currency=c.currency,
        coupon_code=c.coupon_code,
        utm_source=c.utm_source,
        utm_medium=c.utm_medium,
        utm_campaign=c.utm_campaign,
        last_activity_at=c.last_activity_at,
        abandoned_at=c.abandoned_at,
        recovered_at=c.recovered_at,
        recovery_email_sent_at=c.recovery_email_sent_at,
        recovered_order_id=c.recovered_order_id,
        item_count=sum((li.get("quantity") or 0) for li in c.line_items),
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.get(
    "/",
    response_model=SuccessResponse[AbandonedCheckoutListResponse],
    summary="List abandoned checkouts for a store",
    operation_id="list_abandoned_checkouts",
)
async def list_abandoned_checkouts(
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        AbandonedCheckoutRepository, Depends(get_abandoned_checkout_repository)
    ],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    include_recovered: bool = Query(
        False,
        description="Include carts that have already been recovered.",
    ),
    only_recovered: bool = Query(
        False,
        description="Show only recovered carts (e.g. for analytics views).",
    ),
    abandonment_threshold_minutes: int = Query(
        60,
        ge=5,
        le=2880,
        description=(
            "Carts inactive for this many minutes are eligible to be "
            "flipped to `abandoned`. Lazily applied on each list-page load."
        ),
    ),
    has_contact: bool | None = Query(
        None,
        description=(
            "True = only carts with email or phone (recoverable, Shopify "
            "semantics). False = only carts with no contact info. "
            "Omit for everything."
        ),
    ),
):
    """Return the paginated abandoned-checkout feed for the store."""
    if only_recovered:
        recovered_filter: bool | None = True
    elif include_recovered:
        recovered_filter = None
    else:
        recovered_filter = False

    # Lazy abandonment: bulk-flip stale rows before reading. Avoids a
    # dedicated Celery beat job for Phase 4b — the merchant only ever sees
    # rows the threshold considers abandoned. Best-effort; ignore failures.
    if not only_recovered:
        try:
            await repo.mark_stale_as_abandoned(
                store.id, threshold_seconds=abandonment_threshold_minutes * 60
            )
        except Exception:
            pass

    skip = (page - 1) * limit
    items, total = await repo.list_by_store(
        store_id=store.id,
        skip=skip,
        limit=limit,
        abandoned_only=not only_recovered,
        recovered_only=recovered_filter,
        has_contact=has_contact,
    )

    total_pages = (total + limit - 1) // limit if total > 0 else 0

    return SuccessResponse(
        data=AbandonedCheckoutListResponse(
            items=[_to_response(c) for c in items],
            total=total,
            page=page,
            page_size=limit,
            total_pages=total_pages,
        ),
        message="Abandoned checkouts retrieved successfully",
    )


@router.get(
    "/{checkout_id}",
    response_model=SuccessResponse[AbandonedCheckoutResponse],
    summary="Get an abandoned checkout",
    operation_id="get_abandoned_checkout",
)
async def get_abandoned_checkout(
    checkout_id: Annotated[UUID, Path(description="Abandoned-checkout ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        AbandonedCheckoutRepository, Depends(get_abandoned_checkout_repository)
    ],
):
    checkout = await repo.get_by_id(checkout_id)
    if not checkout or checkout.store_id != store.id:
        raise EntityNotFoundError("AbandonedCheckout", str(checkout_id))
    return SuccessResponse(
        data=_to_response(checkout),
        message="Abandoned checkout retrieved successfully",
    )


@router.post(
    "/{checkout_id}/send-recovery-email",
    response_model=SuccessResponse[SendRecoveryEmailResponse],
    summary="Send a recovery email to the abandoned-checkout's customer",
    operation_id="send_recovery_email",
)
async def send_recovery_email(
    checkout_id: Annotated[UUID, Path(description="Abandoned-checkout ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        AbandonedCheckoutRepository, Depends(get_abandoned_checkout_repository)
    ],
):
    """Send a recovery email and stamp `recovery_email_sent_at`.

    The email subject + body are rendered from a future template; for now
    we fall back to the existing Resend service with a minimal HTML body.
    Idempotent — calling twice simply overwrites the timestamp.
    """
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    checkout = await repo.get_by_id(checkout_id)
    if not checkout or checkout.store_id != store.id:
        raise EntityNotFoundError("AbandonedCheckout", str(checkout_id))

    if not checkout.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This checkout has no email address — recovery email cannot be sent",
        )

    if checkout.recovered_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This checkout has already been recovered",
        )

    # Minimal recovery email body — a real template lives in
    # external_services/resend/email_templates and will be wired in alongside
    # the Shopify Recovery flow later (out of scope for Phase 4).
    subject = f"Complete your order at {store.name}"
    items_html = "".join(
        f"<li>{(li.get('product_name') or 'Item')} × {li.get('quantity', 1)}</li>"
        for li in checkout.line_items
    )
    html = (
        f"<p>Hi there,</p>"
        f"<p>You left items in your cart at <strong>{store.name}</strong>.</p>"
        f"<ul>{items_html}</ul>"
        f"<p>Come back and finish your order whenever you're ready.</p>"
    )

    service = ResendEmailService()
    await service.send_email(
        EmailMessage(to=str(checkout.email), subject=subject, html_content=html)
    )

    now = datetime.now(UTC)
    updated = await repo.mark_recovery_email_sent(checkout_id, now)

    return SuccessResponse(
        data=SendRecoveryEmailResponse(
            checkout_id=updated.id,
            email=updated.email or "",
            sent_at=now,
        ),
        message="Recovery email sent",
    )


@router.post(
    "/{checkout_id}/mark-recovered",
    response_model=SuccessResponse[AbandonedCheckoutResponse],
    summary="Manually mark an abandoned checkout as recovered",
    operation_id="mark_abandoned_checkout_recovered",
)
async def mark_abandoned_checkout_recovered(
    checkout_id: Annotated[UUID, Path(description="Abandoned-checkout ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        AbandonedCheckoutRepository, Depends(get_abandoned_checkout_repository)
    ],
    order_id: UUID | None = Query(
        None,
        description="Order ID the checkout was recovered into, if known.",
    ),
):
    """Manually flag a cart as recovered. Used for off-channel conversions
    (merchant called the customer and they ordered via WhatsApp instead)."""
    checkout = await repo.get_by_id(checkout_id)
    if not checkout or checkout.store_id != store.id:
        raise EntityNotFoundError("AbandonedCheckout", str(checkout_id))

    updated = await repo.mark_recovered(checkout_id, order_id=order_id)
    return SuccessResponse(
        data=_to_response(updated),
        message="Checkout marked as recovered",
    )
