"""Single source of truth for Meta CAPI Purchase fan-out across all
payment confirmation paths (Paymob, Fawry, Fawaterak, Instapay,
Kashier, COD).

Plan §5.4 — Purchase is server-authoritative: the browser-side
``fbq('track', 'Purchase', ..., { eventID: order.id })`` fire is
best-effort (script blockers, page abandons after redirect, brand-new
device with no JS context); the webhook hook here is the one Meta
optimizes ad spend against.

Dedup contract:
    event_id = str(order.id)

The storefront's ``BaseOrderConfirmationPage`` passes the same id when
it fires ``Purchase`` from the browser, so Pixel + CAPI collapse to
one event in Meta's Events Manager (~48h dedup window on the tuple
``(pixel_id, event_name, event_id)``).

All callers should:
    try:
        await enqueue_meta_capi_purchase(db, order)
    except Exception:
        log.warning("meta_capi_purchase_enqueue_failed", exc_info=True)

Failures must NEVER fail the webhook. The hourly orphan-purchase sweep
(plan §5.5) catches missed events.
"""

from __future__ import annotations

import contextlib
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger

logger = get_logger(__name__)

# ISO-4217 alphabetic code. Meta rejects anything else, and a composite string
# like "EGP 250" is a real shape merchants' data has produced.
_ISO_4217_RE = re.compile(r"[A-Za-z]{3}")


def _store_host(store: Any) -> str | None:
    """Canonical storefront host for ``store`` — the domain the Pixel set its
    cookies on, and therefore the domain Meta's ``fbc`` subdomain index is
    counted against.

    ``Store.store_url`` already encodes the custom-domain-wins rule (see
    ``link_builder``), so this only has to strip the scheme off it.
    """
    origin = getattr(store, "store_url", None)
    if not origin:
        return None
    with contextlib.suppress(Exception):
        return urlparse(str(origin)).hostname or None
    return None


def _build_user_data_from_order(
    order: Any, *, host: str | None = None
) -> dict[str, Any]:
    """Extract Meta-CAPI user_data from an Order.

    The Meta CAPI client SHA-256-hashes PII downstream (see
    ``meta/hashing.py``) — we forward raw values here so the
    dispatcher stays oblivious to that contract.

    ``ip``, ``user_agent``, ``fbp`` and ``fbc`` come from the order
    metadata snapshot captured at checkout-create time
    (storefront/checkout.py) — using the webhook request's IP would
    attribute the conversion to Paymob/Fawry's data centre, not the
    customer's device, and the ``_fbp``/``_fbc`` cookies simply don't
    exist on a PSP-originated request. ``fbp``/``fbc`` are Meta's two
    highest-coverage non-PII match keys, so omitting them was what left
    the server Purchase matching on the IP alone for guest COD orders.
    Falling back to None when the metadata snapshot is missing (legacy
    orders, COD-via-courier paths) is fine — Meta drops null fields
    server-side.
    """
    from src.infrastructure.external_services.meta.country_iso import (
        canonicalize_country,
    )

    shipping = order.shipping_address or {}
    meta = getattr(order, "metadata", None) or {}
    # Country is free-form on the address ("Egypt", "EG", "مصر"). Meta
    # only indexes the hash of the lowercase ISO-2 code, so non-canonical
    # values would silently miss — run them through the mapper and drop
    # anything unrecognized. ``country_code`` (if explicitly set) takes
    # precedence over the free-form ``country``.
    raw_country = shipping.get("country_code") or shipping.get("country")

    # `_fbc` cookie missing (Pixel blocked, or the landing predated the Pixel
    # being configured) but we still hold the raw click id on the order's own
    # attribution snapshot. /track has rebuilt `fbc` from this for a while;
    # the CONVERSION event — the one Meta optimizes spend against — did not,
    # which is exactly backwards.
    fbc = meta.get("fbc") or _fbc_from_attribution(order, host=host)

    return {
        # `email` is deliberately NOT read from `shipping` — OrderShippingAddress
        # has no email field and never has, so `shipping.get("email")` was a
        # permanent None and every server-authoritative Purchase, Lead and
        # Refund reached Meta with `em: null`. The real value is resolved from
        # the customer record by `fill_identity_from_customer` below.
        "email": None,
        "phone": shipping.get("phone"),
        "first_name": shipping.get("first_name"),
        "last_name": shipping.get("last_name"),
        "city": shipping.get("city"),
        # Governorate / state. Collected at checkout, persisted on the address,
        # and until now dropped on the floor — `st` was not even a key in
        # `hash_user_data`. Free match key on every order.
        "state": shipping.get("state"),
        "country_code": canonicalize_country(raw_country),
        "zip": shipping.get("postal_code") or shipping.get("zip"),
        "customer_id": str(order.customer_id) if order.customer_id else None,
        # The session fingerprint every mid-funnel event already sent as
        # `external_id`. Without it here, Meta saw an anonymous browsing
        # session and an unrelated conversion — the guest journey broke at
        # precisely the event that pays for it. `_external_ids` emits both as
        # an array (customer_id first), so this is purely additive.
        "external_id": getattr(order, "session_fingerprint", None),
        "ip": meta.get("ip_address"),
        "user_agent": meta.get("user_agent"),
        "fbp": meta.get("fbp"),
        "fbc": fbc,
    }


def _fbc_from_attribution(order: Any, *, host: str | None = None) -> str | None:
    """Rebuild ``fbc`` from the order's stored attribution envelope.

    Uses the LANDING timestamp, not "now" and not the payment time — Meta
    wants the moment the ``fbclid`` was first observed.

    ``host`` drives the subdomain index. It was not passed before, so this
    always emitted ``fb.1.…`` while ``/track`` — which does pass one — emitted
    ``fb.2.…`` for the same click on any multi-label domain (``*.com.eg`` is
    the ordinary shape of an Egyptian business domain). Meta then held two
    different click ids for one click, and the one attached to the CONVERSION
    was the malformed one.
    """
    from src.infrastructure.external_services.meta.click_id import synthesize_fbc

    attribution = getattr(order, "attribution", None) or {}
    if not isinstance(attribution, dict):
        return None
    last_touch = attribution.get("last_touch") or attribution.get("first_touch") or {}
    if not isinstance(last_touch, dict):
        return None
    return synthesize_fbc(last_touch.get("fbclid"), last_touch.get("ts"), host=host)


async def fill_identity_from_customer(
    db: AsyncSession, user_data: dict[str, Any], order: Any
) -> None:
    """Fill email / phone / name from the customer record, in place.

    ``OrderShippingAddress`` carries no email — it is not in the value object
    (``core/entities/order.py``) and not in ``_address_to_dict``. So the only
    place an order's email exists is the customer row it points at. Adding a
    field to the address VO would change the persisted order JSON shape
    platform-wide; resolving it here does not.

    Never overwrites a value the address already supplied (what the buyer typed
    for *this* order wins over a stale profile), and never raises — a Purchase
    that reaches Meta with a weaker identity beats one that never fires.

    Placeholder addresses are rejected rather than hashed: guest checkout mints
    synthetic values like ``…@noemail.numueg.app``, and a digest of one can
    never match anything in Meta's index. Sending it would count against the
    event's customer-information completeness while contributing no match —
    the same reasoning ``_country_hash`` already applies to unmappable
    countries.
    """
    customer_id = getattr(order, "customer_id", None)
    if not customer_id:
        return
    if user_data.get("email") and user_data.get("phone"):
        return

    from sqlalchemy import select
    from sqlalchemy.orm import load_only, raiseload

    from src.infrastructure.database.models.tenant.customer import CustomerModel

    try:
        store_id = (
            order.store_id
            if isinstance(order.store_id, UUID)
            else UUID(str(order.store_id))
        )
        cust_id = (
            customer_id if isinstance(customer_id, UUID) else UUID(str(customer_id))
        )
    except (TypeError, ValueError):
        return

    try:
        customer = (
            await db.execute(
                select(CustomerModel)
                .where(
                    CustomerModel.id == cust_id,
                    CustomerModel.store_id == store_id,
                )
                .options(
                    load_only(
                        CustomerModel.id,
                        CustomerModel.email,
                        CustomerModel.phone,
                        CustomerModel.first_name,
                        CustomerModel.last_name,
                    ),
                    raiseload("*"),
                )
            )
        ).scalar_one_or_none()
        if customer is None:
            return
        # Read the attributes INSIDE the guard. This is not defensive noise:
        # `raiseload("*")` turns any unloaded attribute into an exception, and
        # an instance already sitting EXPIRED in this session's identity map
        # (any prior `commit()` expires everything) refreshes itself on first
        # attribute access — which under the async session raises
        # `MissingGreenlet`. Both escape a `try` that only wraps `execute()`,
        # and this function is called from inside payment webhooks, so the
        # escape would fail the webhook rather than just the tracking event.
        email = customer.email
        phone = customer.phone
        first_name = customer.first_name
        last_name = customer.last_name
    except Exception:  # noqa: BLE001 — CAPI must never break a webhook
        return

    if not user_data.get("email") and is_real_email(email):
        user_data["email"] = email
    for key, value in (
        ("phone", phone),
        ("first_name", first_name),
        ("last_name", last_name),
    ):
        if not user_data.get(key) and isinstance(value, str) and value.strip():
            user_data[key] = value


# Domains NUMU itself mints for guests that never supplied an address. A hash
# of one of these matches nothing and dilutes the event.
_PLACEHOLDER_EMAIL_MARKERS = ("@noemail.", "@guest.", "@placeholder.", "@example.com")


def is_real_email(email: str | None) -> bool:
    """True when ``email`` is a genuine address worth hashing for Meta."""
    if not email:
        return False
    value = email.strip().lower()
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        return False
    return not any(marker in value for marker in _PLACEHOLDER_EMAIL_MARKERS)


async def resolve_catalog_ids(db: AsyncSession, order: Any) -> dict[str, str]:
    """``{product_id: meta_catalog_id}`` for the products on this order.

    ``content_ids`` on a conversion event MUST match ``g:id`` in the
    product feed or Meta cannot join the conversion to a catalog row —
    dynamic ads stop attributing revenue, and "viewed but didn't buy"
    retargeting audiences never get cleared by the purchase.

    The feed emits ``meta_catalog_id or product.id``
    (``meta_feed.py:212``) — merchants who already run a Meta catalog
    keyed on their own SKUs set that field. But the conversion events
    were sending the internal UUID unconditionally, so for exactly those
    merchants every AddToCart / InitiateCheckout / Purchase pointed at an
    id the catalog does not contain. Only the PDP's ViewContent honoured
    it (``products/[slug]/page.tsx:319``), which made the mismatch harder
    to spot: the funnel's first event matched and the rest silently did
    not.

    Returns only the products that actually have an override, so callers
    can `.get(pid, pid)` and pay nothing when nobody uses the feature.
    """
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.product import ProductModel

    ids = {
        str(li.get("product_id"))
        for li in (order.line_items or [])
        if li.get("product_id")
    }
    if not ids:
        return {}
    try:
        rows = await db.execute(
            select(ProductModel.id, ProductModel.meta_catalog_id).where(
                ProductModel.id.in_(ids),
                ProductModel.meta_catalog_id.isnot(None),
            )
        )
        return {str(pid): cat for pid, cat in rows.all() if cat}
    except Exception:
        # Never let a catalog-id lookup break a conversion fire — sending
        # the internal UUID is the pre-existing behaviour, not a new risk.
        return {}


async def resolve_catalog_ids_for(
    db: AsyncSession, product_ids: set[str]
) -> dict[str, str]:
    """``{product_id: meta_catalog_id}`` for an arbitrary set of product ids.

    Same contract as ``resolve_catalog_ids`` but decoupled from an Order, so
    the ``/track`` path can apply the identical remap to AddToCart,
    InitiateCheckout, ViewContent and the rest.

    Without this, only the PDP's ViewContent and the server-side Purchase
    honoured the merchant's ``meta_catalog_id``; every other event sent the
    internal UUID. For a merchant whose Meta catalog is keyed on their own
    SKUs that means dynamic ads cannot join the event to a catalog row —
    the highest-ROAS format silently stops attributing, and the mismatch is
    invisible because the funnel's first event matches and the rest don't.
    """
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.product import ProductModel

    clean = {str(pid) for pid in product_ids if pid}
    if not clean:
        return {}
    try:
        rows = await db.execute(
            select(ProductModel.id, ProductModel.meta_catalog_id).where(
                ProductModel.id.in_(clean),
                ProductModel.meta_catalog_id.isnot(None),
            )
        )
        return {str(pid): cat for pid, cat in rows.all() if cat}
    except Exception:  # noqa: BLE001 — never break a fire over a lookup
        return {}


def apply_catalog_ids(custom_data: dict[str, Any], catalog: dict[str, str]) -> None:
    """Rewrite ``content_ids`` and ``contents[].id`` in place, if we have a map.

    No-op when the store uses no catalog overrides, which is the common case —
    the feed emits the internal id for those products too, so the two already
    agree.
    """
    if not catalog or not isinstance(custom_data, dict):
        return

    ids = custom_data.get("content_ids")
    if isinstance(ids, list):
        custom_data["content_ids"] = [
            catalog.get(str(i), i) if isinstance(i, str | int) else i for i in ids
        ]

    contents = custom_data.get("contents")
    if isinstance(contents, list):
        for entry in contents:
            if isinstance(entry, dict) and entry.get("id") is not None:
                entry["id"] = catalog.get(str(entry["id"]), entry["id"])


def validate_conversion_value(
    custom_data: dict[str, Any], event_name: str
) -> str | None:
    """Return a reason string when this payload must NOT be sent, else None.

    Meta reported **0% valid value on website Purchase** for a live NUMU store.
    The cause was browser-side (a guest Purchase fired with no ``value`` at
    all), and this builder was never at fault — it divides integer cents by 100
    exactly once and defaults the currency. This guard exists so that stays
    true: a conversion Meta will reject is not worth sending, and a silent
    ``None`` is exactly how the original defect stayed invisible for months.

    Deliberately permissive in one place: a **zero** value is warned about but
    still sent. A 100%-discounted or fully-gift-carded order is a real
    conversion, and dropping it would trade a reporting blemish for a missing
    sale. Everything genuinely unusable — NaN, infinity, non-numeric, a
    negative on a non-Refund event, a currency that is not three letters — is
    refused, because those can only ever corrupt revenue reporting.
    """
    currency = custom_data.get("currency")
    if not isinstance(currency, str) or not _ISO_4217_RE.fullmatch(currency.strip()):
        return f"invalid_currency:{currency!r}"

    raw_value = custom_data.get("value")
    if isinstance(raw_value, bool) or raw_value is None:
        return f"invalid_value:{raw_value!r}"
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return f"invalid_value:{raw_value!r}"
    if not math.isfinite(value):
        return f"invalid_value:{raw_value!r}"

    # Refund carries a deliberate negative so net revenue reconciles.
    if event_name == "Refund":
        return None if value <= 0 else f"refund_value_not_negative:{value}"
    if value < 0:
        return f"negative_value:{value}"
    return None


def _guard_conversion_payload(
    custom_data: dict[str, Any], event_name: str, order: Any
) -> bool:
    """Normalize currency, alert on anything wrong, and say whether to send."""
    currency = custom_data.get("currency")
    if isinstance(currency, str):
        custom_data["currency"] = currency.strip().upper()

    reason = validate_conversion_value(custom_data, event_name)
    if reason is None:
        if custom_data.get("value") == 0:
            logger.warning(
                "meta_capi_zero_value_conversion",
                extra={"order_id": str(getattr(order, "id", "")), "event": event_name},
            )
        return True

    logger.error(
        "meta_capi_invalid_conversion_payload",
        extra={
            "order_id": str(getattr(order, "id", "")),
            "event": event_name,
            "reason": reason,
        },
    )
    with contextlib.suppress(Exception):
        import sentry_sdk

        sentry_sdk.set_tag("meta_capi.reject_reason", reason.split(":", 1)[0])
        sentry_sdk.capture_message(
            f"meta_capi.invalid_conversion_payload {event_name} {reason}",
            level="error",
        )
    return False


def _build_custom_data_from_order(
    order: Any, catalog_ids: dict[str, str] | None = None
) -> dict[str, Any]:
    """Build the Meta CAPI custom_data dict for an Order.

    ``catalog_ids`` maps product_id → the merchant's Meta catalog id (see
    ``resolve_catalog_ids``). Omitted / empty means every content id falls
    back to the internal UUID, which is what the feed emits for products
    with no override — so the two still agree.

    Forwards the order's UTM attribution (captured by feature 001 at
    checkout-create time from the storefront's ``numu_attribution``
    cookie) into the event payload so Meta's Events Manager can split
    conversions by NUMU marketing campaign instead of collapsing them
    all into ``Direct``.

    Meta accepts arbitrary keys on ``custom_data`` and surfaces them as
    filter / breakdown dimensions, so the merchant can pivot the
    Purchase report by ``numu_utm_campaign`` and see exactly which
    email/SMS campaign drove the sale — even if the user landed via a
    Meta ad first (last-touch wins per Meta's default attribution
    window, but the campaign signal is now visible alongside the ad
    signal for cross-channel reconciliation).
    """
    line_items = order.line_items or []
    catalog = catalog_ids or {}

    def _content_id(li: dict) -> str:
        pid = str(li.get("product_id", ""))
        return catalog.get(pid, pid)

    contents = [
        {
            "id": _content_id(li),
            "quantity": int(li.get("quantity", 1)),
            "item_price": int(li.get("unit_price", 0)) / 100,
        }
        for li in line_items
        if li.get("product_id")
    ]
    data: dict[str, Any] = {
        "value": (order.total or 0) / 100,
        "currency": order.currency or "EGP",
        "content_ids": [_content_id(li) for li in line_items if li.get("product_id")],
        "content_type": "product",
        "contents": contents,
        "num_items": sum(int(li.get("quantity", 1)) for li in line_items),
        "order_id": str(order.id),
    }

    # UTM attribution carry-through. ``utm_campaign`` carries the
    # marketing_campaigns.short_code (Crockford base32, set by the
    # trackable-link builder). ``campaign_id`` is the canonical UUID FK
    # when the short code resolved to a known campaign — Meta sees both
    # so analysts can correlate via either dimension.
    if getattr(order, "utm_source", None):
        data["numu_utm_source"] = order.utm_source
    if getattr(order, "utm_medium", None):
        data["numu_utm_medium"] = order.utm_medium
    if getattr(order, "utm_campaign", None):
        data["numu_utm_campaign"] = order.utm_campaign
        # `numu_campaign_id` is the spec-level alias; keep both for
        # discoverability in Events Manager UI.
        data["numu_campaign_id"] = order.utm_campaign
    if getattr(order, "utm_term", None):
        data["numu_utm_term"] = order.utm_term
    if getattr(order, "utm_content", None):
        data["numu_utm_content"] = order.utm_content
    if getattr(order, "campaign_id", None):
        data["numu_campaign_uuid"] = str(order.campaign_id)

    return data


async def enqueue_meta_capi_event_for_order(
    db: AsyncSession,
    order: Any,
    *,
    event_name: str = "Purchase",
    event_id: str | None = None,
) -> None:
    """Enqueue any Meta CAPI event for an order, gated on store config.

    Wave 2 Phase 12 generalization of ``enqueue_meta_capi_purchase``.
    Supports firing ``Lead`` and ``Purchase`` from the order-status
    event handler when a store has ``purchase_trigger`` /
    ``lead_trigger`` configured for COD-aware timing.

    **Wave 2 Phase 13 — Multi-pixel fan-out.** When the store has
    multiple pixels configured (``store.settings.tracking.meta.pixels``),
    enqueues one task per capi-enabled pixel. Each pixel is a separate
    Meta dedup namespace, so the SAME ``event_id`` is used across all
    fan-out copies — Pixel and CAPI collapse within each pixel's
    Events Manager. Backward-compatible: stores with only the legacy
    single ``pixel_id`` set still get exactly one enqueue.

    Dedup contract: ``event_id`` defaults to ``str(order.id)`` for
    ``Purchase`` (matches the storefront's browser-side fire so Meta
    collapses them within ~48h). For non-Purchase events (e.g.
    ``Lead`` on COD-confirmation), defaults to a prefixed form
    ``f"{event_name_lower}-{order.id}"`` so Lead and Purchase dedupe
    separately within Meta's window.
    """
    # Lazy imports — the Celery task module pulls in the full HTTP
    # client + signing stack; the repository imports the SQLAlchemy
    # tenant model. Keeping them lazy means webhook handlers without
    # CAPI configured pay zero import cost on cold start.
    from src.application.services.meta_pixel_resolver import resolve_pixels
    from src.infrastructure.messaging.tasks.meta_capi import enqueue_capi_event
    from src.infrastructure.repositories.store_repository import StoreRepository

    sr = StoreRepository(db)
    store_id_uuid = (
        order.store_id
        if isinstance(order.store_id, UUID)
        else UUID(str(order.store_id))
    )
    store = await sr.get_by_id(store_id_uuid)
    if store is None:
        return
    meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}

    pixels = resolve_pixels(meta_cfg, mode="capi")
    if not pixels:
        return

    if event_id is None:
        event_id = (
            str(order.id)
            if event_name == "Purchase"
            else f"{event_name.lower()}-{order.id}"
        )

    paid_at = getattr(order, "paid_at", None) or datetime.now(UTC)
    user_data = _build_user_data_from_order(order, host=_store_host(store))
    await fill_identity_from_customer(db, user_data, order)
    custom_data = _build_custom_data_from_order(
        order, await resolve_catalog_ids(db, order)
    )
    if not _guard_conversion_payload(custom_data, event_name, order):
        return
    event_time = int(paid_at.timestamp())

    # ``action_source: website`` events without an event_source_url are
    # flagged "Missing event_source_url" in Events Manager and lose match
    # quality. The Celery task defaults to the store origin, but we already
    # have both the store and the order here, so send the REAL page the
    # customer landed on — the storefront's order-confirmation route
    # (numu-storefront: app/[domain]/checkout/[order_id]/thank-you). Guarded
    # because a store with no domain/subdomain/slug resolution yields
    # nothing usable, and a malformed URL is worse than none.
    store_origin = getattr(store, "store_url", None)
    event_source_url = (
        f"{store_origin}/checkout/{order.id}/thank-you" if store_origin else None
    )

    # Fan out: same event_id across pixels (each pixel is its own Meta
    # dedup namespace). Per-pixel tasks are independent Celery jobs so
    # one pixel's 4xx doesn't block the others.
    for pixel in pixels:
        # Through the shared door, which persists a conversion before it
        # touches the broker. Production Redis evicts under memory pressure
        # (`maxmemory-policy allkeys-lru`), and an evicted Purchase left no
        # trace anywhere — the row now exists before the message does.
        # `db` is the webhook's own session, so an order that rolls back
        # takes the outbox row with it.
        await enqueue_capi_event(
            session=db,
            store=store,
            tenant_id=getattr(store, "tenant_id", None),
            store_id=str(order.store_id),
            pixel_id=pixel.pixel_id,
            event_name=event_name,
            event_id=event_id,
            event_time=event_time,
            event_source_url=event_source_url,
            user_data=user_data,
            custom_data=custom_data,
            action_source="website",
        )


async def enqueue_meta_capi_purchase(db: AsyncSession, order: Any) -> None:
    """Enqueue a Purchase CAPI event for ``order``, gated on store config.

    Thin wrapper around ``enqueue_meta_capi_event_for_order`` preserved
    for the existing payment-webhook callers (Paymob, Fawry, Fawaterak,
    Instapay, Kashier, COD).
    """
    await enqueue_meta_capi_event_for_order(db, order, event_name="Purchase")


async def enqueue_meta_capi_refund(db: AsyncSession, order: Any) -> None:
    """Wave 2 Phase 21 — fire a Meta CAPI Refund custom event.

    Sends ``event_name="Refund"`` with a NEGATIVE ``value`` so the
    merchant can build a Meta Ads Manager custom report subtracting
    Refund from Purchase to see real net revenue. Meta's native CAPI
    spec has no built-in refund event (Shopify doesn't fire one either,
    so Meta-reported revenue diverges from actuals platform-wide) —
    this custom event closes the gap.

    Dedup contract: ``event_id = f"refund-{order.id}"`` — namespaced
    away from the original Purchase event_id so they appear as separate
    events in Events Manager (the merchant's custom report joins them).

    Fans out to every capi-enabled pixel (Phase 13 multi-pixel parity).
    """
    from src.application.services.meta_pixel_resolver import resolve_pixels
    from src.infrastructure.messaging.tasks.meta_capi import enqueue_capi_event
    from src.infrastructure.repositories.store_repository import StoreRepository

    sr = StoreRepository(db)
    store_id_uuid = (
        order.store_id
        if isinstance(order.store_id, UUID)
        else UUID(str(order.store_id))
    )
    store = await sr.get_by_id(store_id_uuid)
    if store is None:
        return
    meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
    pixels = resolve_pixels(meta_cfg, mode="capi")
    if not pixels:
        return

    custom_data = _build_custom_data_from_order(
        order, await resolve_catalog_ids(db, order)
    )
    # Override the value to be negative — this is the contract Meta
    # custom-event-based refund reports key on. The absolute value is
    # the same as the original Purchase, so the merchant's "Net Meta
    # revenue" report is exactly ``Purchase_total + Refund_total``.
    custom_data["value"] = -abs(custom_data.get("value") or 0.0)
    custom_data["refund_for_order_id"] = str(order.id)

    user_data = _build_user_data_from_order(order, host=_store_host(store))
    await fill_identity_from_customer(db, user_data, order)
    if not _guard_conversion_payload(custom_data, "Refund", order):
        return
    event_time = int(datetime.now(UTC).timestamp())
    event_id = f"refund-{order.id}"

    for pixel in pixels:
        await enqueue_capi_event(
            session=db,
            store=store,
            tenant_id=getattr(store, "tenant_id", None),
            store_id=str(order.store_id),
            pixel_id=pixel.pixel_id,
            event_name="Refund",
            event_id=event_id,
            event_time=event_time,
            event_source_url=None,
            user_data=user_data,
            custom_data=custom_data,
            action_source="system_generated",
        )
