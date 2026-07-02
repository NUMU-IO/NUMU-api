"""Ingest a TikTok Shop order into NUMU as a native order.

Mirrors ``OrderImportService`` (the established external-order pattern): find-
or-create the customer, dedup by ``metadata["external_order_id"]`` via
``OrderRepository.exists_by_external_id``, build the ``Order`` entity directly
(with ``source`` recorded in ``metadata``), persist via ``OrderRepository.create``,
and publish ``OrderCreatedEvent`` so the normal fulfillment/notification
pipeline runs.

TikTok Shop payloads vary by API version; every field read here is defensive
(``.get`` with fallbacks) so a shape drift skips gracefully rather than raising.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from src.core.entities.customer import Customer
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.core.events.order_events import OrderCreatedEvent
from src.core.logging import get_logger
from src.core.value_objects.email import Email

logger = get_logger(__name__)

# TikTok Shop order-status string → NUMU OrderStatus.
_STATUS_MAP: dict[str, OrderStatus] = {
    "UNPAID": OrderStatus.PENDING,
    "ON_HOLD": OrderStatus.PENDING,
    "AWAITING_SHIPMENT": OrderStatus.CONFIRMED,
    "AWAITING_COLLECTION": OrderStatus.PROCESSING,
    "PARTIALLY_SHIPPING": OrderStatus.PROCESSING,
    "IN_TRANSIT": OrderStatus.SHIPPED,
    "DELIVERED": OrderStatus.DELIVERED,
    "COMPLETED": OrderStatus.DELIVERED,
    "CANCELLED": OrderStatus.CANCELLED,
}


def _placeholder_email(phone: str, store_slug: str) -> str:
    digits = re.sub(r"\D", "", phone or "") or "unknown"
    return f"tiktok-{digits}@{store_slug}.placeholder"


def _to_cents(amount: Any) -> int:
    try:
        return int(round(float(amount) * 100))
    except (TypeError, ValueError):
        return 0


class TikTokShopOrderIngestor:
    """Maps a TikTok Shop order dict → a native NUMU order."""

    def __init__(self, order_repo, customer_repo, event_bus=None) -> None:
        self.order_repo = order_repo
        self.customer_repo = customer_repo
        self.event_bus = event_bus

    async def ingest(self, *, store, tiktok_order: dict[str, Any]) -> str | None:
        """Create (or skip if duplicate) one order. Returns the NUMU order id.

        ``store`` is the resolved Store entity (carries id, tenant_id, slug,
        default_currency). Returns ``None`` when the order was a duplicate or
        couldn't be mapped.
        """
        external_id = str(tiktok_order.get("id") or tiktok_order.get("order_id") or "")
        if not external_id:
            logger.warning("tiktok_shop_ingest_no_order_id")
            return None

        # ── Dedup ─────────────────────────────────────────────────────
        if await self.order_repo.exists_by_external_id(store.id, external_id):
            logger.info("tiktok_shop_ingest_duplicate", external_id=external_id)
            return None

        recipient = (
            tiktok_order.get("recipient_address") or tiktok_order.get("recipient") or {}
        )
        phone = str(recipient.get("phone_number") or recipient.get("phone") or "")
        full_name = str(recipient.get("name") or recipient.get("full_name") or "")
        first = str(recipient.get("first_name") or "") or (
            full_name.split(" ", 1)[0] if full_name else "TikTok"
        )
        last = str(recipient.get("last_name") or "") or (
            full_name.split(" ", 1)[1] if " " in full_name else "—"
        )

        # City / region from district_info (list of admin levels) or a flat field.
        city = ""
        districts = recipient.get("district_info") or []
        if isinstance(districts, list) and districts:
            city = str(districts[-1].get("address_name") or "")
        city = city or str(recipient.get("city") or recipient.get("region") or "")

        address_line = str(
            recipient.get("full_address")
            or recipient.get("address_detail")
            or recipient.get("address_line1")
            or "—"
        )

        # ── Customer find-or-create ───────────────────────────────────
        email_raw = str(tiktok_order.get("buyer_email") or "").strip() or (
            _placeholder_email(phone, store.slug)
        )
        try:
            email_vo = Email(value=email_raw)
        except Exception:
            email_vo = Email(value=_placeholder_email(phone, store.slug))

        customer = await self.customer_repo.get_by_email(store.id, email_vo)
        if customer is None:
            customer = await self.customer_repo.create(
                Customer(
                    store_id=store.id,
                    email=email_vo,
                    first_name=first,
                    last_name=last or "—",
                    phone=phone or None,
                    is_verified=False,
                    metadata={"source": "tiktok_shop"},
                ),
                tenant_id=store.tenant_id,
            )

        # ── Line items ────────────────────────────────────────────────
        payment = tiktok_order.get("payment") or {}
        currency = str(
            payment.get("currency")
            or tiktok_order.get("currency")
            or (
                store.default_currency.value
                if hasattr(store.default_currency, "value")
                else "EGP"
            )
        )
        raw_items = tiktok_order.get("line_items") or tiktok_order.get("items") or []
        line_items: list[OrderLineItem] = []
        subtotal = 0
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            qty = int(it.get("quantity") or 1)
            unit = _to_cents(it.get("sale_price") or it.get("unit_price") or 0)
            total_price = unit * qty
            subtotal += total_price
            line_items.append(
                OrderLineItem(
                    product_id=uuid4(),  # placeholder — TikTok SKUs aren't NUMU catalog ids
                    product_name=str(
                        it.get("product_name") or it.get("name") or "Item"
                    ),
                    sku=it.get("seller_sku") or it.get("sku_id"),
                    quantity=qty,
                    unit_price=unit,
                    total_price=total_price,
                )
            )

        # Fallback single synthetic line if the payload carried no items.
        grand_total = _to_cents(payment.get("total_amount") or payment.get("total"))
        if not line_items:
            amount = grand_total or 0
            line_items = [
                OrderLineItem(
                    product_id=uuid4(),
                    product_name="TikTok Shop order",
                    quantity=1,
                    unit_price=amount,
                    total_price=amount,
                )
            ]
            subtotal = amount

        shipping_fee = _to_cents(payment.get("shipping_fee"))
        total = grand_total or (subtotal + shipping_fee)

        status = _STATUS_MAP.get(
            str(tiktok_order.get("status") or "").upper(), OrderStatus.CONFIRMED
        )
        payment_status = (
            PaymentStatus.PENDING
            if status == OrderStatus.PENDING
            else PaymentStatus.PAID
        )

        shipping = OrderShippingAddress(
            first_name=first,
            last_name=last or "—",
            address_line1=address_line,
            city=city or "—",
            country=str(recipient.get("region_code") or "EG"),
            phone=phone or None,
        )

        order_number = await self.order_repo.get_next_order_number(store.id)

        order = Order(
            store_id=store.id,
            tenant_id=store.tenant_id,
            customer_id=customer.id,
            order_number=order_number,
            line_items=line_items,
            shipping_address=shipping,
            status=status,
            payment_status=payment_status,
            subtotal=subtotal,
            shipping_cost=shipping_fee,
            total=total,
            currency=currency,
            payment_method="tiktok_shop",
            metadata={"source": "tiktok_shop", "external_order_id": external_id},
        )
        created = await self.order_repo.create(order)

        if self.event_bus and status != OrderStatus.DRAFT:
            try:
                self.event_bus.publish(
                    OrderCreatedEvent(
                        order_id=created.id,
                        order_number=created.order_number,
                        store_id=created.store_id,
                        customer_id=created.customer_id,
                        total=float(created.total),
                        currency=created.currency,
                    )
                )
            except Exception:
                pass

        logger.info(
            "tiktok_shop_order_ingested",
            order_id=str(created.id),
            external_id=external_id,
            total=total,
        )
        return str(created.id)
