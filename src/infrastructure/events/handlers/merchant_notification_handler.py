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
from src.infrastructure.database.models.tenant.customer import CustomerModel
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

        # ── Customer name (best-effort, for a friendlier email) ─────────
        customer: CustomerModel | None = (
            await session.execute(
                select(CustomerModel).where(CustomerModel.id == event.customer_id)
            )
        ).scalar_one_or_none()
        customer_name = (
            f"{customer.first_name} {customer.last_name}".strip() if customer else None
        )

        tenant_id: UUID | None = store.tenant_id
        store_name = store.name
        language = _normalize_language(store.default_language)

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
            total_cents=event.total,
            currency=event.currency,
            customer_name=customer_name,
            order_url=order_url,
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
