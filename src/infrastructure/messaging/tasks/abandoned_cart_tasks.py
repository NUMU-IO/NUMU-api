"""Celery tasks for abandoned cart detection and recovery notifications.

Detects carts that have been inactive for 1+ hours and sends
WhatsApp (primary) or email (fallback) reminders to customers.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from src.config import settings
from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop = None


def run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


ABANDONED_CART_THRESHOLD_HOURS = 1  # Notify after 1 hour of inactivity
ABANDONED_CART_MAX_AGE_HOURS = 72  # Don't notify carts older than 3 days
NOTIFICATION_COOLDOWN_KEY = "abandoned_cart_notified:{store_id}:{customer_id}"
NOTIFICATION_COOLDOWN_SECONDS = 86400  # Don't re-notify same customer within 24h


@celery_app.task(
    name="tasks.detect_abandoned_carts",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def detect_abandoned_carts_task(self):
    """Scan Redis for abandoned carts and queue notifications.

    Runs every 30 minutes via Celery Beat. Scans all customer carts,
    identifies those inactive for 1+ hours, and sends recovery messages.
    """
    try:
        result = run_async(_detect_and_notify())
        logger.info(f"Abandoned cart scan complete: {result}")
        return result
    except Exception as exc:
        logger.exception("Abandoned cart detection failed")
        raise self.retry(exc=exc)


async def _detect_and_notify() -> dict:
    """Core abandoned cart detection logic."""

    from src.infrastructure.cache.redis_cache import RedisCacheService
    from src.infrastructure.repositories.cart_repository import RedisCartRepository

    cart_repo = RedisCartRepository()
    cache = RedisCacheService() if settings.redis_host else None
    client = await cart_repo._get_client()

    now = datetime.now(UTC)
    threshold = now - timedelta(hours=ABANDONED_CART_THRESHOLD_HOURS)
    max_age = now - timedelta(hours=ABANDONED_CART_MAX_AGE_HOURS)

    stats = {"scanned": 0, "abandoned": 0, "notified": 0, "skipped": 0, "errors": 0}

    # Scan all customer cart keys
    async for key in client.scan_iter(match="cart:customer:*"):
        stats["scanned"] += 1
        try:
            raw = await client.get(key)
            if not raw:
                continue

            cart_data = json.loads(raw)
            customer_id = cart_data.get("customer_id")
            store_id = cart_data.get("store_id")
            items = cart_data.get("items", [])
            updated_at_str = cart_data.get("updated_at")

            # Skip empty carts or carts without customer
            if not customer_id or not store_id or not items:
                continue

            # Parse updated_at
            if not updated_at_str:
                continue
            try:
                updated_at = datetime.fromisoformat(
                    updated_at_str.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                continue

            # Check if cart is abandoned (inactive for threshold period)
            if updated_at > threshold:
                continue  # Too recent, not abandoned yet

            # Check if cart is too old (don't nag)
            if updated_at < max_age:
                continue

            stats["abandoned"] += 1

            # Check cooldown — don't re-notify same customer within 24h
            if cache:
                cooldown_key = NOTIFICATION_COOLDOWN_KEY.format(
                    store_id=store_id, customer_id=customer_id
                )
                if await cache.exists(cooldown_key):
                    stats["skipped"] += 1
                    continue

            # Queue notification
            _queue_abandoned_cart_notification(
                customer_id=customer_id,
                store_id=store_id,
                cart_data=cart_data,
            )

            # Set cooldown
            if cache:
                await cache.set(cooldown_key, "1", expire=NOTIFICATION_COOLDOWN_SECONDS)

            stats["notified"] += 1

        except Exception as e:
            logger.warning(f"Error processing cart key {key}: {e}")
            stats["errors"] += 1

    if cache:
        await cache.close()
    await cart_repo.close()

    return stats


def _queue_abandoned_cart_notification(
    customer_id: str,
    store_id: str,
    cart_data: dict,
) -> None:
    """Queue WhatsApp + email notifications for an abandoned cart."""
    send_abandoned_cart_notification_task.delay(
        customer_id=customer_id,
        store_id=store_id,
        cart_items_count=len(cart_data.get("items", [])),
        cart_subtotal=sum(
            item.get("unit_price", 0) * item.get("quantity", 0)
            for item in cart_data.get("items", [])
        ),
        cart_currency=cart_data.get("currency", "EGP"),
    )


@celery_app.task(
    name="tasks.send_abandoned_cart_notification",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_abandoned_cart_notification_task(
    self,
    customer_id: str,
    store_id: str,
    cart_items_count: int,
    cart_subtotal: int,
    cart_currency: str = "EGP",
):
    """Send abandoned cart recovery notification via WhatsApp + email fallback."""
    try:
        result = run_async(
            _send_notification(
                customer_id=customer_id,
                store_id=store_id,
                cart_items_count=cart_items_count,
                cart_subtotal=cart_subtotal,
                cart_currency=cart_currency,
            )
        )
        logger.info(
            f"Abandoned cart notification sent: customer={customer_id}, "
            f"store={store_id}, channel={result.get('channel')}"
        )
        return result
    except Exception as exc:
        logger.error(f"Abandoned cart notification failed: {exc}")
        raise self.retry(exc=exc)


async def _send_notification(
    customer_id: str,
    store_id: str,
    cart_items_count: int,
    cart_subtotal: int,
    cart_currency: str,
) -> dict:
    """Send recovery notification via WhatsApp (primary) or email (fallback)."""
    from uuid import UUID

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.customer import CustomerModel
    from src.infrastructure.database.models.tenant.store import StoreModel

    async with AsyncSessionLocal() as session:
        # Fetch customer
        from sqlalchemy import select

        customer_result = await session.execute(
            select(CustomerModel).where(CustomerModel.id == UUID(customer_id))
        )
        customer = customer_result.scalar_one_or_none()
        if not customer:
            return {"sent": False, "reason": "customer_not_found"}

        # Fetch store
        store_result = await session.execute(
            select(StoreModel).where(StoreModel.id == UUID(store_id))
        )
        store = store_result.scalar_one_or_none()
        if not store:
            return {"sent": False, "reason": "store_not_found"}

        # Check store notification preferences
        store_settings = store.settings or {}
        notification_settings = store_settings.get("notifications", {}).get(
            "whatsapp", {}
        )
        abandoned_cart_enabled = notification_settings.get("abandoned_cart", True)

        customer_name = (
            f"{customer.first_name or ''} {customer.last_name or ''}".strip()
            or "عميلنا"
        )
        customer_phone = customer.phone
        customer_email = customer.email
        store_name = store.name
        store_language = store.default_language or "ar"
        cart_value = f"{cart_currency} {cart_subtotal / 100:.2f}"

        sent = False
        channel = "none"

        # Try WhatsApp first
        if abandoned_cart_enabled and settings.whatsapp_enabled and customer_phone:
            try:
                from src.infrastructure.external_services.whatsapp.messaging_service import (
                    WhatsAppMessagingService,
                )

                wa_service = WhatsAppMessagingService()
                phone = wa_service._format_phone_number(str(customer_phone))

                import httpx

                url = f"https://graph.facebook.com/v18.0/{wa_service.phone_number_id}/messages"
                payload = {
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "template",
                    "template": {
                        "name": f"abandoned_cart_{store_language}",
                        "language": {"code": store_language},
                        "components": [
                            {
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": customer_name},
                                    {"type": "text", "text": cart_value},
                                    {"type": "text", "text": store_name},
                                ],
                            },
                        ],
                    },
                }

                async with httpx.AsyncClient() as http_client:
                    resp = await http_client.post(
                        url,
                        json=payload,
                        headers=wa_service._get_headers(),
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        sent = True
                        channel = "whatsapp"
                        logger.info(f"Abandoned cart WhatsApp sent to {phone}")
                    else:
                        logger.warning(
                            f"WhatsApp abandoned cart failed: {resp.status_code}"
                        )
            except Exception as e:
                logger.warning(f"WhatsApp abandoned cart error: {e}")

        # Fallback to email
        if not sent and customer_email:
            try:
                from src.core.interfaces.services.email_service import EmailMessage
                from src.infrastructure.external_services.resend.email_service import (
                    ResendEmailService,
                )

                email_service = ResendEmailService()

                subject = (
                    f"نسيت حاجة في سلتك! 🛒 — {store_name}"
                    if store_language == "ar"
                    else f"You left something in your cart! 🛒 — {store_name}"
                )

                if store_language == "ar":
                    html = f"""
                    <div dir="rtl" style="font-family: 'Cairo', Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 24px;">
                        <h2 style="text-align: center; margin-bottom: 8px;">سلتك مستنياك! 🛒</h2>
                        <p style="text-align: center; color: #666; font-size: 14px;">
                            أهلاً {customer_name}، عندك {cart_items_count} منتجات في سلتك بقيمة {cart_value}
                        </p>
                        <div style="text-align: center; margin: 24px 0;">
                            <a href="https://{store.subdomain}.numueg.app/checkout"
                               style="display: inline-block; padding: 12px 32px; background: #111; color: #fff; text-decoration: none; font-weight: bold; font-size: 14px;">
                                أكمل طلبك الآن
                            </a>
                        </div>
                        <p style="text-align: center; color: #999; font-size: 12px;">
                            {store_name}
                        </p>
                    </div>
                    """
                else:
                    html = f"""
                    <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 24px;">
                        <h2 style="text-align: center; margin-bottom: 8px;">Your cart is waiting! 🛒</h2>
                        <p style="text-align: center; color: #666; font-size: 14px;">
                            Hi {customer_name}, you have {cart_items_count} items in your cart worth {cart_value}
                        </p>
                        <div style="text-align: center; margin: 24px 0;">
                            <a href="https://{store.subdomain}.numueg.app/checkout"
                               style="display: inline-block; padding: 12px 32px; background: #111; color: #fff; text-decoration: none; font-weight: bold; font-size: 14px;">
                                Complete your order
                            </a>
                        </div>
                        <p style="text-align: center; color: #999; font-size: 12px;">
                            {store_name}
                        </p>
                    </div>
                    """

                await email_service.send_email(
                    EmailMessage(
                        to=str(customer_email),
                        subject=subject,
                        html_content=html,
                    )
                )
                sent = True
                channel = "email"
                logger.info(f"Abandoned cart email sent to {customer_email}")
            except Exception as e:
                logger.warning(f"Email abandoned cart error: {e}")

        return {
            "sent": sent,
            "channel": channel,
            "customer_id": customer_id,
            "store_id": store_id,
            "cart_value": cart_value,
        }
