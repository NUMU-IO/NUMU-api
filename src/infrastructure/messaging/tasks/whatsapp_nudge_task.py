"""Celery task: send WhatsApp COD-to-Prepaid conversion nudge.

Creates a payment link session and sends a WhatsApp message with
the payment URL to the customer.  Triggered by the ``whatsapp_confirm``
automation action and the dashboard's manual "WhatsApp confirm" action.

The heavy lifting lives in
:mod:`src.application.services.shopify_nudge_service` so the synchronous
``resend-verification`` endpoint and the recovery ladder share the exact
same session-mint + template send.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import httpx

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


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
    shop_domain: str = "",
    store_name: str = "",
    language: str = "ar",
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
    shop_domain:
        ``*.myshopify.com`` domain — used to derive a display store name
        when ``store_name`` is empty.
    store_name:
        Merchant-facing store name for the message copy.
    language:
        ``"ar"`` or ``"en"`` template language.
    """

    async def _run() -> dict:
        from sqlalchemy import text

        from src.application.services.shopify_nudge_service import (
            create_payment_link_session,
            send_conversion_nudge,
            store_display_name,
        )
        from src.infrastructure.database.connection import AsyncSessionLocal

        sid = UUID(store_id)

        async with AsyncSessionLocal() as session:
            await session.execute(text("SET search_path TO public"))
            pls = await create_payment_link_session(
                session,
                store_id=sid,
                shopify_order_id=shopify_order_id,
                amount_cents=amount_cents,
                currency=currency,
            )
            session_id = str(pls.id)
            await session.commit()

        if not customer_phone:
            logger.warning(
                "No phone number for WhatsApp nudge: order=%s", shopify_order_id
            )
            from src.application.services.shopify_nudge_service import (
                payment_page_url,
            )

            return {
                "session_id": session_id,
                "payment_url": payment_page_url(session_id),
                "whatsapp_sent": False,
                "reason": "no_phone",
            }

        result = await send_conversion_nudge(
            phone=customer_phone,
            customer_name=customer_name,
            order_number=order_number,
            store_name=store_name or store_display_name(shop_domain),
            amount_cents=amount_cents,
            currency=currency,
            payment_session_id=session_id,
            language=language,
        )
        out: dict = {
            "session_id": session_id,
            "payment_url": result.payment_url,
            "whatsapp_sent": result.sent,
        }
        if result.message_id:
            out["message_id"] = result.message_id
        if result.error:
            out["reason"] = result.error
        return out

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
