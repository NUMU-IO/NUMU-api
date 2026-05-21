"""Public order-tracking endpoint.

Exposes a single GET /storefront/track/{order_id} that returns a
*sanitised* view of an order — just what the customer needs to see on
their tracking page, without leaking payment IDs, emails, or full
addresses. Protected only by the order UUID (128 bits of entropy, same
approach Shopify uses for its /orders/:token URLs).

The URL is stable for the order's lifetime, so it's safe to embed in
the confirmation email and WhatsApp message — refreshing the page
picks up whatever status the merchant most recently set in the dashboard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel

from src.api.dependencies.repositories import (
    get_order_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.store_repository import StoreRepository

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TrackingLineItem(BaseModel):
    product_name: str
    quantity: int
    unit_price: int  # cents
    total: int  # cents
    product_image_url: str | None = None


class TrackingShippingAddress(BaseModel):
    """Partial — enough for the customer to confirm they gave the right
    address without exposing street-level detail to a random URL visitor."""

    city: str | None = None
    governorate: str | None = None
    country: str | None = None


class TrackingStore(BaseModel):
    name: str
    subdomain: str | None = None
    logo_url: str | None = None
    custom_domain: str | None = None


class TrackingTimeline(BaseModel):
    """Per-status ISO timestamps. Nulls for stages not yet reached."""

    placed_at: datetime | None = None
    paid_at: datetime | None = None
    fulfilled_at: datetime | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None


class OrderTrackingResponse(BaseModel):
    order_id: str
    order_number: str
    status: str  # pending / confirmed / processing / shipped / delivered / cancelled
    payment_status: str  # pending / paid / failed / refunded
    fulfillment_status: str  # unfulfilled / fulfilled / partially_fulfilled
    payment_method: str | None = None
    currency: str
    subtotal: int
    shipping_cost: int
    tax_amount: int
    discount_amount: int
    total: int
    customer_name: str | None = None
    shipping_address: TrackingShippingAddress
    line_items: list[TrackingLineItem]
    tracking_number: str | None = None
    tracking_url: str | None = None
    shipping_method: str | None = None
    timeline: TrackingTimeline
    store: TrackingStore


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/track/{order_id}",
    response_model=SuccessResponse[OrderTrackingResponse],
    summary="Get public tracking view of an order",
    operation_id="track_order",
)
async def track_order(
    order_id: Annotated[
        UUID, Path(description="Order UUID — from the confirmation email/WA link")
    ],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Public tracking view for an order. No auth required — protected
    only by the unguessable order UUID. Returns a sanitised subset of the
    order fields; notably omits: customer email/phone, exact street,
    payment provider IDs, internal notes.
    """
    order = await order_repo.get_by_id(order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found.",
        )

    store = await store_repo.get_by_id(order.store_id)
    if store is None:
        # Store deleted while order survives — treat as 404 rather than
        # leaking that the order exists.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found.",
        )

    ship = order.shipping_address
    customer_name = f"{ship.first_name or ''} {ship.last_name or ''}".strip() or None

    items = [
        TrackingLineItem(
            product_name=li.product_name,
            quantity=li.quantity,
            unit_price=li.unit_price,
            total=li.quantity * li.unit_price,
            product_image_url=getattr(li, "product_image_url", None),
        )
        for li in order.line_items
    ]

    return SuccessResponse(
        data=OrderTrackingResponse(
            order_id=str(order.id),
            order_number=order.order_number,
            status=order.status.value
            if hasattr(order.status, "value")
            else str(order.status),
            payment_status=order.payment_status.value
            if hasattr(order.payment_status, "value")
            else str(order.payment_status),
            fulfillment_status=order.fulfillment_status.value
            if hasattr(order.fulfillment_status, "value")
            else str(order.fulfillment_status),
            payment_method=order.payment_method,
            currency=order.currency,
            subtotal=order.subtotal,
            shipping_cost=order.shipping_cost,
            tax_amount=order.tax_amount,
            discount_amount=order.discount_amount,
            total=order.total,
            customer_name=customer_name,
            shipping_address=TrackingShippingAddress(
                city=ship.city,
                governorate=getattr(ship, "governorate", None)
                or getattr(ship, "state", None),
                country=getattr(ship, "country", None),
            ),
            line_items=items,
            tracking_number=order.tracking_number,
            tracking_url=order.tracking_url,
            shipping_method=order.shipping_method,
            timeline=TrackingTimeline(
                placed_at=order.created_at,
                paid_at=order.paid_at,
                fulfilled_at=order.fulfilled_at,
                shipped_at=order.shipped_at,
                delivered_at=order.delivered_at,
                cancelled_at=order.cancelled_at,
            ),
            store=TrackingStore(
                name=store.name,
                subdomain=store.subdomain,
                logo_url=store.logo_url,
                custom_domain=store.custom_domain,
            ),
        ),
        message="Order tracking retrieved",
    )
