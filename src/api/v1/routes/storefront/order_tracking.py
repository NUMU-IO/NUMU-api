"""Public order-tracking endpoints.

Two ways into the same *sanitised* view of an order — just what the
customer needs to see on their tracking page, without leaking payment
IDs, emails, or full addresses:

* ``GET  /storefront/track/{order_id}`` — the link we embed in the
  confirmation email and WhatsApp message. Protected only by the order
  UUID (128 bits of entropy, same approach Shopify uses for its
  /orders/:token URLs). The URL is stable for the order's lifetime, so
  refreshing the page picks up whatever status the merchant most
  recently set in the dashboard.
* ``POST /storefront/store/{store_id}/track/lookup`` — the guest form on
  the storefront's ``/track`` page, for the customer who lost that link.
  Takes the order number plus one verification key (phone or email) and
  answers with the identical payload, so the page can then redirect to
  the canonical UUID URL.

The lookup endpoint is the only tracking surface where the secret is
guessable: order numbers are short and sequential. Every miss on it
answers with one indistinguishable 404 (see ``_order_not_found``) and it
is rate-limited two ways in ``src/api/middleware/rate_limit.py``: the
per-IP tier the middleware applies (``_is_track_lookup``), plus the
content-keyed per-order and per-store budgets this module spends itself
(``enforce_track_lookup_budgets``). The second pair exists because the
first is only as trustworthy as ``X-Forwarded-For``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.responses import JSONResponse

from src.api.dependencies.repositories import (
    get_customer_repository,
    get_order_repository,
    get_product_repository,
    get_shipment_repository,
    get_store_repository,
)
from src.api.middleware.rate_limit import enforce_track_lookup_budgets
from src.api.responses import SuccessResponse
from src.application.services.carrier_resolver import tracking_url_for
from src.application.services.shipment_public_view import public_shipment
from src.core.entities.order import Order
from src.core.entities.store import Store
from src.core.logging import get_logger
from src.infrastructure.repositories.customer_repository import CustomerRepository
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.product_repository import ProductRepository
from src.infrastructure.repositories.shipment_repository import ShipmentRepository
from src.infrastructure.repositories.store_repository import StoreRepository

logger = get_logger(__name__)

router = APIRouter()

# Second router because the two endpoints mount at different prefixes: the
# UUID route stays store-less at /storefront (the links in confirmation emails
# already shipped that way), while the lookup needs the store scope to make a
# short order number unique. A `store_id` path param can't live on a router
# whose mount point doesn't supply one, so they can't share `router`.
lookup_router = APIRouter()

# C0 controls + DEL. Checked after `.strip()`, so this only catches bytes
# *inside* the number, never the trailing newline a paste leaves behind.
_CONTROL_CHARS = frozenset(chr(c) for c in [*range(0x00, 0x20), 0x7F])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TrackingLineItem(BaseModel):
    product_name: str
    quantity: int
    unit_price: int  # cents
    total: int  # cents
    product_image_url: str | None = None
    # Catalog identifier for the browser Purchase event's `content_ids`.
    #
    # Not PII: it is the same id already embedded in every product URL and in
    # `product_image_url` above. Exposing it lets a GUEST thank-you page send
    # `content_ids` — without it, guests (most COD buyers) fired Purchase with
    # no product attribution at all, so Meta could not clear them from
    # "viewed but didn't buy" retargeting audiences or credit the catalog.
    # Prefers the merchant's Meta catalog id so the event joins the feed.
    product_id: str | None = None


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


class TrackingShipmentEvent(BaseModel):
    """One step of the parcel's journey.

    Status and timestamp only. The internal description is never exposed
    — it carries raw carrier errors and merchant-written reasons, and
    these endpoints are public.
    """

    status: str
    label_en: str
    label_ar: str
    occurred_at: datetime | None = None


class TrackingShipment(BaseModel):
    """The parcel, as a customer may see it."""

    carrier: str | None = None
    tracking_number: str | None = None
    #: None for a manual courier — NUMU issued the number and there is no
    #: carrier site to link to. This page is the tracking page.
    tracking_url: str | None = None
    status: str | None = None
    status_label_en: str | None = None
    status_label_ar: str | None = None
    delivered_at: datetime | None = None
    events: list[TrackingShipmentEvent] = Field(default_factory=list)


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
    #: The parcel's own progress. None when nothing has been shipped
    #: yet — the order timeline above still applies.
    shipment: TrackingShipment | None = None
    store: TrackingStore


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class OrderLookupRequest(BaseModel):
    """Guest lookup: an order number plus exactly one verification key.

    Requiring one key (not both) keeps the form a single field for the
    customer — guest checkouts collect a phone but email is optional, and
    an emailed receipt may be all a returning customer still has.
    """

    # Capped at the orders.order_number column width (String(50)) — anything
    # longer can't match a row, so reject it before it reaches the query.
    order_number: str = Field(min_length=1, max_length=50)
    phone: str | None = None
    email: str | None = None

    @field_validator("order_number")
    @classmethod
    def no_control_characters(cls, v: str) -> str:
        """Strip surrounding whitespace, then refuse embedded control bytes.

        Outer ``\\r\\n``/tabs come from pasting the number out of a receipt
        and must keep behaving exactly as before (they strip away, and the
        lookup answers the usual 404 or 200).

        An *embedded* control byte is different. PostgreSQL cannot encode
        NUL, so asyncpg aborts the SELECT with CharacterNotInRepertoireError
        (``invalid byte sequence for encoding "UTF8": 0x00``) and the request
        dies as an unhandled 500 — the one response shape on this endpoint a
        caller can force at will, and the only input that escapes the
        uniform 404. Rejecting here keeps the value away from the query
        entirely; it cannot leak anything, because the decision is made on
        the input's shape alone, before any row is read.
        """
        v = v.strip()
        if any(ch in v for ch in _CONTROL_CHARS):
            raise ValueError("order_number contains invalid control characters.")
        return v

    @model_validator(mode="after")
    def exactly_one_key(self) -> OrderLookupRequest:
        """Blank strings count as absent — the storefront form posts the
        untouched field as ``""``, and treating that as "supplied" would
        let a caller pass an empty key and skip verification entirely.
        """
        self.phone = (self.phone or "").strip() or None
        self.email = (self.email or "").strip() or None
        if bool(self.phone) == bool(self.email):
            raise ValueError(
                "Provide exactly one of 'phone' or 'email' to verify the order."
            )
        return self


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _order_not_found() -> HTTPException:
    """The single 404 both endpoints answer every miss with.

    On the lookup endpoint this is a security property, not tidiness:
    distinguishing "no such order number" from "that phone doesn't match"
    would turn the short, sequential order numbers into an oracle for
    enumerating a store's order volume and customer list.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Order not found.",
    )


def _normalise_order_number(raw: str) -> str:
    """Trim and drop a leading ``#``.

    Order numbers are rendered as ``#ORD-1042`` on the receipt and in the
    dashboard, so that's what customers copy back into the form.
    """
    return raw.strip().removeprefix("#").strip()


def _phone_key(raw: str | None) -> str | None:
    """Reduce a phone number to a comparable national form.

    Egyptian customers write the same number five ways and all of them
    turn up in this form: ``+201098433918`` (E.164 — what the checkout
    normaliser stores), ``00201098433918`` (the international prefix still
    printed on older receipts), ``01098433918`` (how everyone actually
    says it), bare ``1098433918``, and any of those with spaces or dashes
    pasted out of WhatsApp. Comparing the raw strings would fail the
    ownership check and 404 a legitimate customer holding their own order.

    So: strip every separator, drop the international access code and the
    ``20`` country code however they were written, then re-add the national
    trunk ``0``. Non-Egyptian numbers (Saudi stores are live too) keep
    their country code but still collapse ``00966…`` onto ``+966…``, so
    both sides of the comparison agree with themselves.
    """
    if raw is None:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if digits.startswith("00"):
        # The pre-"+" way of writing E.164, still printed on older
        # receipts. No Egyptian (or Saudi) national number starts "00",
        # so this can't eat a real local prefix.
        digits = digits[2:]
    if digits.startswith("20") and len(digits) > 10:
        # Length-guarded so a *national* number that happens to begin with
        # "20" (a 10-digit landline form) isn't mistaken for the Egyptian
        # country code and truncated.
        digits = digits[2:]
    if not digits.startswith("0"):
        digits = f"0{digits}"
    return digits


async def _build_tracking_response(
    order: Order,
    store: Store,
    product_repo: ProductRepository,
    shipment_repo: ShipmentRepository | None = None,
) -> SuccessResponse[OrderTrackingResponse]:
    """Assemble the sanitised tracking payload.

    Shared by both endpoints so the UUID link and the guest lookup can
    never drift into exposing different field sets.
    """
    ship = order.shipping_address
    customer_name = f"{ship.first_name or ''} {ship.last_name or ''}".strip() or None

    # The parcel's own journey. Until now these endpoints never read the
    # shipment at all, so a customer saw "shipped" and a bare number —
    # and for a manual courier, a number with nowhere to go.
    shipment_view: TrackingShipment | None = None
    if shipment_repo is not None:
        try:
            shipments = await shipment_repo.get_by_order(order.id)
            forward = [s for s in shipments if s.shipment_type == "forward"]
            if forward:
                latest = forward[-1]
                shipment_view = TrackingShipment(
                    **public_shipment(
                        latest,
                        tracking_url=tracking_url_for(
                            latest.carrier, latest.tracking_number
                        ),
                    )
                )
        except Exception:
            # Tracking must still render if the shipment lookup fails —
            # the order timeline is the fallback, and a customer chasing
            # a parcel should not meet an error page.
            logger.warning("tracking_shipment_lookup_failed", order_id=str(order.id))

    # Order line items don't snapshot the product image, so resolve the
    # current primary image (absolute CDN URL) by product_id in one batch.
    image_by_pid: dict[str, str] = {}
    pids: list[UUID] = []
    for li in order.line_items:
        raw_pid = getattr(li, "product_id", None)
        if not raw_pid:
            continue
        try:
            pids.append(UUID(str(raw_pid)))
        except (ValueError, TypeError):
            continue
    # Same batch resolves the merchant's Meta catalog id, so the Purchase
    # event's `content_ids` join to their product feed rather than to an
    # internal UUID the catalog does not contain.
    catalog_by_pid: dict[str, str] = {}
    if pids:
        for product in await product_repo.get_by_ids(pids):
            if product.images:
                image_by_pid[str(product.id)] = product.images[0]
            catalog_id = getattr(product, "meta_catalog_id", None)
            if catalog_id:
                catalog_by_pid[str(product.id)] = str(catalog_id)

    items = [
        TrackingLineItem(
            product_name=li.product_name,
            quantity=li.quantity,
            unit_price=li.unit_price,
            total=li.quantity * li.unit_price,
            product_image_url=image_by_pid.get(
                str(getattr(li, "product_id", "") or "")
            ),
            product_id=(
                catalog_by_pid.get(str(getattr(li, "product_id", "") or ""))
                or (str(getattr(li, "product_id", "")) or None)
            ),
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
            shipment=shipment_view,
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


# ---------------------------------------------------------------------------
# Endpoints
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
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
    expected_store: Annotated[
        str | None,
        Query(
            alias="store",
            description=(
                "Expected store subdomain/custom-domain/id. When provided, the "
                "order must belong to it (else 404). The storefront and newly "
                "generated tracking links pass this to scope the lookup to the "
                "tenant; omitting it preserves the legacy UUID-only behaviour."
            ),
        ),
    ] = None,
) -> SuccessResponse[OrderTrackingResponse]:
    """Public tracking view for an order. No auth required — protected
    only by the unguessable order UUID (and, when supplied, the ``store``
    scope). Returns a sanitised subset of the order fields; notably omits:
    customer email/phone, exact street, payment provider IDs, internal notes.
    """
    order = await order_repo.get_by_id(order_id)
    if order is None:
        raise _order_not_found()

    store = await store_repo.get_by_id(order.store_id)
    if store is None:
        # Store deleted while order survives — treat as 404 rather than
        # leaking that the order exists.
        raise _order_not_found()

    # Tenant scoping (defense-in-depth): when the caller asserts a store, the
    # order must belong to it — this stops one tenant's storefront from
    # resolving another tenant's order by UUID. Same 404 as a missing order so
    # existence isn't leaked.
    if expected_store and expected_store.strip():
        exp = expected_store.strip().lower()
        if exp not in {
            (store.subdomain or "").lower(),
            (store.custom_domain or "").lower(),
            str(order.store_id).lower(),
        }:
            raise _order_not_found()

    return await _build_tracking_response(order, store, product_repo, shipment_repo)


@lookup_router.post(
    "/track/lookup",
    response_model=SuccessResponse[OrderTrackingResponse],
    summary="Look up an order by number + phone/email (guest)",
    operation_id="storefront_lookup_order_for_tracking",
)
async def lookup_order_for_tracking(
    store_id: Annotated[
        UUID, Path(description="Store the order belongs to — authoritative scope")
    ],
    payload: OrderLookupRequest,
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
) -> SuccessResponse[OrderTrackingResponse] | JSONResponse:
    """Resolve an order from its number plus one verification key, for the
    customer who no longer has their tracking link.

    Returns exactly the payload the UUID route returns — including
    ``data.order_id``, which the storefront uses to move the customer onto
    the canonical ``/track/{order_id}`` URL.

    No auth: the order number alone is guessable, so the phone/email key
    is what actually authorises the read. Wrong number, wrong key, and
    order-belongs-to-another-store all answer the same 404 — see
    ``_order_not_found``. A 429 (``JSONResponse``, same shape the rate-limit
    middleware emits) means a lookup budget is spent.
    """
    # Spend the content-keyed budgets before touching the database. The
    # middleware's per-IP tier can be side-stepped by anyone willing to vary
    # X-Forwarded-For; these are keyed on the store and the order number
    # themselves, so they hold whatever the caller claims about its address.
    # Deliberately first: a 429 that depended on the order existing would be
    # the enumeration oracle the uniform 404 below is here to deny.
    throttled = await enforce_track_lookup_budgets(store_id, payload.order_number)
    if throttled is not None:
        return throttled

    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise _order_not_found()

    # store_id from the path is the authoritative scope: the repository
    # query filters on it (on top of the tenant RLS filter), so an order
    # number from a different tenant simply doesn't resolve here.
    order = await order_repo.get_by_order_number(
        store_id, _normalise_order_number(payload.order_number)
    )
    if order is None:
        raise _order_not_found()

    if payload.phone:
        supplied = _phone_key(payload.phone)
        stored = _phone_key(order.shipping_address.phone)
        if supplied is None or stored is None or supplied != stored:
            raise _order_not_found()
    else:
        # Email lives on the customer record, not the order — guest
        # checkouts still create one, so customer_id is always populated.
        customer = await customer_repo.get_by_id(order.customer_id)
        if customer is None:
            raise _order_not_found()
        supplied_email = (payload.email or "").strip().lower()
        if not supplied_email or str(customer.email).strip().lower() != supplied_email:
            raise _order_not_found()

    return await _build_tracking_response(order, store, product_repo, shipment_repo)
