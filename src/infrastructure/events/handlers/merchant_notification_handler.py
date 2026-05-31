"""Merchant email notification handler for new orders.

Sends an email to the store owner whenever a customer places an order, so
the merchant knows—per store—that a new order is waiting to be fulfilled.

Subscribed to ``OrderCreatedEvent`` in
``src/infrastructure/events/setup.py``. Runs post-commit in its own DB
session (the deferred dispatcher guarantees the order/store rows are
already committed). Mirrors the resolve-in-own-session pattern used by the
WhatsApp + order-activity handlers.

Merchants can opt out per store via
``store.settings.email_notifications.new_order`` (defaults to True).
"""

from uuid import UUID

from sqlalchemy import select

from src.config import settings
from src.config.logging_config import get_logger
from src.core.events.order_events import OrderCreatedEvent
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)


def _normalize_language(raw: str | None) -> str:
    """Collapse a store's default_language to the two locales the merchant
    email template supports ('ar' / 'en'). Anything non-English → Arabic."""
    return "en" if (raw or "ar").lower().startswith("en") else "ar"


async def handle_merchant_order_notification(event: OrderCreatedEvent) -> None:
    """Email the store owner when a new order is created.

    Best-effort: any missing piece (store, owner email, opt-out) results in
    a structured skip log and a silent return — a notification failure must
    never affect order creation.
    """
    async with AsyncSessionLocal() as session:
        # ── Store: name, owner, tenant, settings, language ──────────────
        store: StoreModel | None = (
            await session.execute(
                select(StoreModel).where(StoreModel.id == event.store_id)
            )
        ).scalar_one_or_none()
        if store is None:
            logger.warning(
                "merchant_order_email_skipped",
                order_id=str(event.order_id),
                reason="store_not_found",
            )
            return

        store_settings = store.settings or {}
        email_prefs = store_settings.get("email_notifications", {}) or {}
        # Absent key means enabled — opt-out, not opt-in.
        if not email_prefs.get("new_order", True):
            logger.info(
                "merchant_order_email_skipped",
                order_id=str(event.order_id),
                store_id=str(event.store_id),
                reason="merchant_opted_out",
            )
            return

        # ── Recipient: store owner's account email, then contact_email ──
        owner: UserModel | None = (
            await session.execute(
                select(UserModel).where(UserModel.id == store.owner_id)
            )
        ).scalar_one_or_none()
        recipient = (owner.email if owner else None) or store.contact_email
        if not recipient:
            logger.warning(
                "merchant_order_email_skipped",
                order_id=str(event.order_id),
                store_id=str(event.store_id),
                reason="no_merchant_email",
            )
            return

        # ── Order: line items + totals + creation date for the summary ──
        order: OrderModel | None = (
            await session.execute(
                select(OrderModel).where(OrderModel.id == event.order_id)
            )
        ).scalar_one_or_none()
        if order is not None:
            items = [
                {
                    "name": li.get("product_name") or "",
                    "quantity": li.get("quantity", 1),
                    "total_cents": li.get("total_price")
                    or (li.get("unit_price", 0) * li.get("quantity", 1)),
                }
                for li in (order.line_items or [])
            ]
            products_value_cents = order.subtotal
            total_cents = order.total
            shipping_cents = order.shipping_cost or None
            currency = order.currency or event.currency
            created_at = order.created_at
        else:
            # Order row not visible yet (shouldn't happen post-commit) — fall
            # back to the event's total so the merchant still gets notified.
            items = []
            products_value_cents = int(event.total or 0)
            total_cents = int(event.total or 0)
            shipping_cents = None
            currency = event.currency
            created_at = None

        # Greet the merchant by their own name (the recipient), not the
        # customer's. Owner may be None when we fell back to contact_email.
        owner_name = owner.first_name if owner else None

        tenant_id: UUID | None = store.tenant_id
        store_name = store.name
        language = _normalize_language(store.default_language)
        # Per-store timezone for the order-date line (Egypt UTC+2 default).
        timezone_name = (store_settings.get("timezone") or "").strip() or "Africa/Cairo"

    # Deep link to the order in the merchant hub (route: /orders/:orderId).
    order_url = f"{settings.merchant_hub_url.rstrip('/')}/orders/{event.order_id}"

    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    service = ResendEmailService()
    try:
        result = await service.send_merchant_new_order(
            email=recipient,
            order_number=event.order_number,
            store_name=store_name,
            products_value_cents=products_value_cents,
            currency=currency,
            items=items,
            customer_name=owner_name,
            order_url=order_url,
            created_at=created_at,
            timezone_name=timezone_name,
            shipping_cents=shipping_cents,
            total_cents=total_cents,
            language=language,
            store_id=event.store_id,
            tenant_id=tenant_id,
        )
    except Exception:
        logger.exception(
            "merchant_order_email_failed",
            order_id=str(event.order_id),
            store_id=str(event.store_id),
        )
        return

    logger.info(
        "merchant_order_email_sent",
        order_id=str(event.order_id),
        store_id=str(event.store_id),
        email=recipient,
        success=result,
    )
