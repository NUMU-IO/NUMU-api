"""Celery tasks for abandoned cart detection and recovery notifications.

Detects carts that have been inactive for 1+ hours and sends
WhatsApp (primary) or email (fallback) reminders to customers.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx

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

    # ── Pass 2 setup: anonymous phone-bearing checkouts (checkout-identity) ──
    # The Redis scan below only sees `cart:customer:*`, so every anonymous
    # cart was invisible to recovery. The identity layer now attaches a phone
    # to abandoned_checkouts rows BEFORE any customer exists (typed at the
    # OTP prompt or the save-cart nudge), which makes those rows recoverable
    # — swept after the customer-cart scan.

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

    # ── Pass 2: anonymous abandoned checkouts that carry a phone ────────
    try:
        anon_stats = await _sweep_anonymous_phone_checkouts(
            cache, threshold=threshold, max_age=max_age
        )
        stats["anon_candidates"] = anon_stats["candidates"]
        stats["anon_notified"] = anon_stats["notified"]
        stats["anon_skipped"] = anon_stats["skipped"]
        stats["errors"] += anon_stats["errors"]
    except Exception:
        logger.exception("anonymous_abandoned_checkout_sweep_failed")
        stats["errors"] += 1

    if cache:
        await cache.close()
    await cart_repo.close()

    return stats


async def _sweep_anonymous_phone_checkouts(
    cache,
    *,
    threshold: datetime,
    max_age: datetime,
) -> dict:
    """Nudge anonymous abandoned checkouts whose phone we captured.

    These rows have ``customer_id IS NULL`` — no Redis customer cart exists,
    so the scan above can never find them. The phone arrived via the
    checkout-identity layer (typed at the OTP prompt / save-cart nudge, or
    at the checkout contact form) and the pre-OTP-attach decision means even
    a customer who typed a phone and bailed is reachable.

    Recovery link: ``/cart/<subdomain>/<checkout_id>`` — the recover
    endpoint's FIRST resolution branch is exactly an abandoned_checkouts id
    (cart_sdk_aliases._resolve_recover_line_items), so no new plumbing.

    Consent posture mirrors the merchant-triggered notify_whatsapp route:
    entering a phone at checkout is treated as implied consent for cart
    recovery, explicit opt-out is honoured, and the SAME cooldown key is
    shared with the manual button so auto + manual can never double-send
    within 24h. Merchant toggle (whatsapp_notifications.abandoned_cart,
    default OFF) gates the whole pass per store.
    """
    from sqlalchemy import select

    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.abandoned_checkout import (
        AbandonedCheckoutModel,
    )
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service
    from src.infrastructure.repositories.whatsapp_opt_in_repository import (
        WhatsAppOptInRepository,
    )

    stats = {"candidates": 0, "notified": 0, "skipped": 0, "errors": 0}

    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AbandonedCheckoutModel)
                    .where(
                        AbandonedCheckoutModel.phone.isnot(None),
                        AbandonedCheckoutModel.customer_id.is_(None),
                        AbandonedCheckoutModel.recovered_at.is_(None),
                        AbandonedCheckoutModel.last_activity_at <= threshold,
                        AbandonedCheckoutModel.last_activity_at >= max_age,
                    )
                    .order_by(AbandonedCheckoutModel.last_activity_at.desc())
                    # Bounded per run; the 30-min beat catches the rest next
                    # cycle, and the cooldown makes re-selection harmless.
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )
        stats["candidates"] = len(rows)
        if not rows:
            return stats

        # One store fetch per distinct store, not per row.
        store_ids = {row.store_id for row in rows}
        stores = (
            (
                await session.execute(
                    select(StoreModel).where(StoreModel.id.in_(store_ids))
                )
            )
            .scalars()
            .all()
        )
        stores_by_id = {s.id: s for s in stores}
        optin_repo = WhatsAppOptInRepository(session)

        for row in rows:
            try:
                store = stores_by_id.get(row.store_id)
                if store is None or not store.subdomain:
                    stats["skipped"] += 1
                    continue
                store_settings = store.settings or {}
                wa_notifs = store_settings.get("whatsapp_notifications", {}) or {}
                # Same default-OFF marketing gate as the customer-cart pass.
                if not bool(wa_notifs.get("abandoned_cart", False)):
                    stats["skipped"] += 1
                    continue

                phone = str(row.phone)

                # Shared cooldown with stores/abandoned_checkouts.notify_whatsapp
                # — the merchant's manual nudge and this auto one are one
                # budget, not two.
                cooldown_key = f"abandoned_cart_notified:{row.store_id}:phone:{phone}"
                if cache and await cache.exists(cooldown_key):
                    stats["skipped"] += 1
                    continue

                if await optin_repo.has_opt_out(row.store_id, phone):
                    stats["skipped"] += 1
                    continue

                # Transport nuance: GOWA renders the nudge locally; the Meta
                # path uses the approved abandoned_cart_v2 template and a
                # failed send (e.g. not approved for this store) is simply
                # logged below — mirroring the legacy customer-cart pass,
                # which does no pre-check either.
                service = await get_whatsapp_service(
                    row.store_id, session, store.tenant_id
                )
                language = (
                    "en"
                    if (store.default_language or "ar").lower().startswith("en")
                    else "ar"
                )
                result = await service.send_abandoned_cart(
                    MessageRecipient(phone=phone, language=language),
                    store.name,
                    cart_token=f"{store.subdomain}/{row.id}",
                )
                if result.success:
                    stats["notified"] += 1
                    if cache:
                        await cache.set(
                            cooldown_key, "1", expire=NOTIFICATION_COOLDOWN_SECONDS
                        )
                    # Merchant-visible marker on the row itself.
                    extra = dict(row.extra_data or {})
                    extra["wa_auto_nudge_at"] = datetime.now(UTC).isoformat()
                    row.extra_data = extra
                    await session.commit()
                else:
                    stats["skipped"] += 1
                    logger.info(
                        "anon_abandoned_nudge_send_failed",
                        extra={
                            "checkout_id": str(row.id),
                            "store_id": str(row.store_id),
                            "error_code": result.error_code,
                        },
                    )
            except Exception:
                stats["errors"] += 1
                logger.exception(
                    "anon_abandoned_nudge_row_failed",
                    extra={"checkout_id": str(getattr(row, "id", None))},
                )

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
    # backend-030 / US6 / FR-031 — exponential backoff over up to 5
    # attempts. Non-retriable errors raised from the body skip the
    # autoretry path (FR-032). DLQ writeback is wired into the task
    # body's final-failure branch.
    autoretry_for=(httpx.HTTPError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
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

        # Check store notification preferences. The canonical merchant
        # toggle lives at store.settings.whatsapp_notifications.abandoned_cart
        # — the same path the merchant-hub WhatsApp settings page writes and
        # the order-lifecycle handlers read. (The legacy
        # settings.notifications.whatsapp.* path was never written by the UI,
        # so the toggle here was effectively always-on regardless of the
        # merchant's choice.)
        store_settings = store.settings or {}
        wa_notifs = store_settings.get("whatsapp_notifications", {}) or {}
        # Default OFF: abandoned cart is a MARKETING send; the merchant must
        # explicitly opt in via the WhatsApp settings toggle.
        abandoned_cart_enabled = bool(wa_notifs.get("abandoned_cart", False))

        customer_name = (
            f"{customer.first_name or ''} {customer.last_name or ''}".strip()
            or "عميلنا"
        )
        customer_phone = customer.phone
        customer_email = customer.email
        store_name = store.name
        store_language = store.default_language or "ar"
        # abandoned_cart_v2 is seeded for {"en", "ar"} — normalize the store
        # language to one of those for the template lookup.
        wa_language = "en" if store_language.lower().startswith("en") else "ar"
        cart_value = f"{cart_currency} {cart_subtotal / 100:.2f}"
        _ = cart_value  # retained for the email fallback below

        sent = False
        channel = "none"

        # Try WhatsApp first — via the shared messaging service so the
        # send uses the approved abandoned_cart_v2 template (body +
        # Complete-purchase URL button) instead of an ad-hoc payload with a
        # non-existent template name.
        if abandoned_cart_enabled and settings.whatsapp_enabled and customer_phone:
            try:
                from src.core.interfaces.services.messaging_service import (
                    MessageRecipient,
                )
                from src.infrastructure.external_services.whatsapp.messaging_service import (
                    WhatsAppMessagingService,
                )

                wa_service = WhatsAppMessagingService()
                recipient = MessageRecipient(
                    phone=str(customer_phone),
                    name=customer_name,
                    language=wa_language,
                )
                # cart_token = `<subdomain>/<customer_id>`; the apex
                # /cart/<subdomain>/<id> redirector forwards to the
                # storefront's /api/cart/recover route, which rebuilds this
                # customer's live cart into the shopper's session so they
                # land on /cart with the items restored (customer_id resolves
                # the still-live Redis cart in the recover endpoint).
                result = await wa_service.send_abandoned_cart(
                    recipient,
                    store_name,
                    cart_token=(
                        f"{store.subdomain}/{customer_id}" if store.subdomain else ""
                    ),
                )
                if result.success:
                    sent = True
                    channel = "whatsapp"
                    logger.info(f"Abandoned cart WhatsApp sent to {customer_phone}")
                else:
                    logger.warning(
                        f"WhatsApp abandoned cart failed: {result.error_message}"
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
