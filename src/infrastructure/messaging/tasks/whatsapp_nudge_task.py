"""Celery task: send WhatsApp COD-to-Prepaid conversion nudge.

Creates a payment link session and sends a WhatsApp message with
the payment URL to the customer.  Triggered by the ``whatsapp_confirm``
automation action.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None

_PAY_BASE_URL = "https://pay.numu.app"
_DEFAULT_EXPIRY_HOURS = 24


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.send_whatsapp_nudge",
    bind=True,
    # backend-030 / US6 / FR-031 — exponential backoff for retriable
    # transport errors. NonRetriableWhatsAppError is intentionally NOT
    # in the autoretry tuple so it short-circuits to DLQ (FR-032).
    autoretry_for=(httpx.HTTPError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    soft_time_limit=60,
)
def send_whatsapp_nudge(
    self,
    store_id: str,
    shopify_order_id: str,
    amount_cents: int,
    currency: str,
    customer_phone: str,
    customer_name: str,
    order_number: str,
) -> dict:
    """Create a payment link session and send a WhatsApp nudge.

    Parameters
    ----------
    store_id:
        UUID string of the store.
    shopify_order_id:
        Shopify order ID.
    amount_cents:
        Order total in cents.
    currency:
        Currency code (e.g. ``"EGP"``).
    customer_phone:
        Customer phone number (raw, will be formatted).
    customer_name:
        Customer display name.
    order_number:
        Shopify order number for the message.
    """

    async def _run() -> dict:
        from sqlalchemy import text

        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.database.models.tenant.payment_link_session import (
            PaymentLinkSessionModel,
        )
        from src.infrastructure.database.models.tenant.shopify_app_settings import (
            ShopifyAppSettingsModel,
        )

        sid = UUID(store_id)

        async with AsyncSessionLocal() as session:
            await session.execute(text("SET search_path TO public"))

            # 1. Create payment link session
            from sqlalchemy import select

            settings_row = await session.execute(
                select(ShopifyAppSettingsModel).where(
                    ShopifyAppSettingsModel.store_id == sid
                )
            )
            settings = settings_row.scalar_one_or_none()

            gateways = ["paymob"]
            if settings and settings.paymob_connected:
                gateways = ["paymob"]

            pls = PaymentLinkSessionModel(
                store_id=sid,
                shopify_order_id=shopify_order_id,
                amount_cents=amount_cents,
                currency=currency,
                available_gateways=gateways,
                expires_at=datetime.now(UTC) + timedelta(hours=_DEFAULT_EXPIRY_HOURS),
            )
            session.add(pls)
            await session.flush()

            payment_url = f"{_PAY_BASE_URL}/{pls.id}"
            session_id = str(pls.id)

            await session.commit()

        # 2. Send WhatsApp message
        if not customer_phone:
            logger.warning(
                "No phone number for WhatsApp nudge: order=%s", shopify_order_id
            )
            return {
                "session_id": session_id,
                "payment_url": payment_url,
                "whatsapp_sent": False,
                "reason": "no_phone",
            }

        whatsapp_sent = False
        try:
            from src.core.interfaces.services.messaging_service import (
                MessageContent,
                MessageRecipient,
                MessageType,
            )
            from src.infrastructure.external_services.whatsapp.messaging_service import (
                WhatsAppMessagingService,
            )

            wa = WhatsAppMessagingService()
            if not wa.enabled:
                logger.info("WhatsApp disabled — nudge URL generated only")
            else:
                amount_display = f"{amount_cents / 100:,.2f} {currency}"
                recipient = MessageRecipient(
                    phone=customer_phone,
                    name=customer_name or "Customer",
                    language="ar",
                )
                content = MessageContent(
                    type=MessageType.ORDER_CONFIRMATION,
                    recipient=recipient,
                    template_params={
                        "customer_name": customer_name or "Customer",
                        "order_number": order_number,
                        "total": amount_display,
                        "store_name": "NUMU",
                        "payment_link": payment_url,
                    },
                )
                result = await wa.send_message(content)
                whatsapp_sent = result.success
                if result.success:
                    logger.info(
                        "WhatsApp nudge sent for order %s: message_id=%s",
                        shopify_order_id,
                        result.message_id,
                    )
                else:
                    logger.warning(
                        "WhatsApp nudge failed for order %s: %s",
                        shopify_order_id,
                        result.error_message,
                    )
        except Exception as exc:
            logger.error("WhatsApp nudge error for order %s: %s", shopify_order_id, exc)

        return {
            "session_id": session_id,
            "payment_url": payment_url,
            "whatsapp_sent": whatsapp_sent,
        }

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error(
            "WhatsApp nudge task failed for order %s (store %s): %s",
            shopify_order_id,
            store_id,
            exc,
            exc_info=True,
        )
        exc_str = str(exc).lower()
        if any(kw in exc_str for kw in ("connection", "timeout", "unavailable")):
            raise self.retry(exc=exc)
        return {
            "error": str(exc),
            "whatsapp_sent": False,
        }
