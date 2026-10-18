"""Celery tasks for abandoned cart detection and recovery notifications.

Detects carts that have been inactive for 1+ hours and sends
WhatsApp (primary) or email (fallback) reminders to customers.

Both passes (anonymous checkouts with a phone, logged-in customers' carts)
apply the same rules, in this order:

1. The checkout is open: last active 1-72 h ago, not recovered, and the
   shopper has placed no order since, whether or not that order was
   attributed back to the checkout row.
2. The hub notification and ``checkout.abandoned`` fire once per checkout
   row, whatever the plan or the merchant's switch says.
3. The plan includes recovery (the ``abandoned_cart`` entitlement).
4. The merchant's switch, ``whatsapp_notifications.abandoned_cart``, is on
   (default OFF). It is the only reminders switch the hub offers, and the
   Abandoned checkouts page shows it as Active / Not active, so it gates the
   email fallback as well: email has no switch of its own.
5. Nobody has reminded this checkout yet, neither the automation nor the
   merchant's buttons, and the shopper got no reminder in the last 24 h.
6. Consent. WhatsApp honours the shopper's opt-out (typing a phone at
   checkout is the implied consent the manual nudge also relies on). Email
   goes only to a real address of a customer who accepted marketing, and
   only when WhatsApp did not reach them.
7. Quiet hours. Nothing reaches a shopper between 22:00 and 09:00 store time
   (``store.settings.timezone``, default Africa/Cairo). A reminder due then
   is queued with a Celery ETA of 09:00 plus up to 30 minutes, so a night's
   backlog does not land at once, and all the rules above are checked again
   when it runs. The hub notification and the webhook are not held, and the
   merchant's manual nudge is never held.

WhatsApp goes through the store's own transport, so the store's paid
WhatsApp access and its message allowance apply, and each send is logged.
"""

import asyncio
import json
import logging
import random
from datetime import UTC, datetime, timedelta

import httpx

from src.application.services.entitlement_service import EntitlementService
from src.application.services.notification_feed import (
    emit_notification_standalone,
)
from src.config import settings
from src.core.utils.store_timezone import resolve_store_timezone_name, safe_zone
from src.core.value_objects.phone import PhoneNumber
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _recovery_included(session, store) -> bool:
    """Recovery is a plan feature (decision D5). Every store that existed
    when it became one keeps it through a grandfather override."""
    tenant = (
        await session.get(TenantModel, store.tenant_id) if store.tenant_id else None
    )
    return tenant is not None and await EntitlementService(session).has(
        tenant, "abandoned_cart"
    )


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
QUIET_FROM_HOUR = 22  # rule 7, on the store's clock
QUIET_UNTIL_HOUR = 9
QUIET_SPREAD_SECONDS = 30 * 60


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _e164(phone: str) -> str:
    """E.164 when the phone parses (``01…`` becomes ``+201…``), else as typed."""
    return PhoneNumber(value=phone).value


def phone_cooldown_key(store_id, phone: str) -> str:
    """The 24 h budget for one phone, shared by both passes and the merchant's
    manual nudge. Keyed on E.164: a checkout row can hold ``01…`` where the
    customer record holds ``+201…``, and both are the same shopper."""
    return f"abandoned_cart_notified:{store_id}:phone:{_e164(phone)}"


def _reminders_on(store) -> bool:
    """Rule 4: the merchant's switch, default OFF (abandoned-cart reminders
    are marketing)."""
    notifs = (store.settings or {}).get("whatsapp_notifications") or {}
    return bool(notifs.get("abandoned_cart", False))


def _reminded(row) -> bool:
    """Rule 5: this checkout was reminded already, by the automation or by
    the merchant's email or WhatsApp button."""
    extra = row.extra_data or {}
    return bool(
        row.recovery_email_sent_at
        or extra.get("wa_auto_nudge_at")
        or extra.get("last_whatsapp_notified_at")
    )


def _checkout_dedupe_key(checkout_id) -> str:
    """Rule 2: one feed row, and so one ``checkout.abandoned``, per checkout,
    from whichever pass sees it first. The ``anon`` is historical: rows the
    anonymous pass already announced must keep matching."""
    return f"cart.abandoned:anon:{checkout_id}"


def _held_key(checkout_id) -> str:
    return f"abandoned_cart_held:{checkout_id}"


def _quiet_until(store) -> datetime | None:
    """Rule 7: None by day. At night, when to send instead (UTC): 09:00 on
    the store's clock plus a random spread."""
    zone = safe_zone(resolve_store_timezone_name(store.settings))
    local = _utcnow().astimezone(zone)
    if QUIET_UNTIL_HOUR <= local.hour < QUIET_FROM_HOUR:
        return None
    morning = local.replace(hour=QUIET_UNTIL_HOUR, minute=0, second=0, microsecond=0)
    if local.hour >= QUIET_FROM_HOUR:
        morning += timedelta(days=1)
    spread = timedelta(seconds=random.randint(0, QUIET_SPREAD_SECONDS))
    return morning.astimezone(UTC) + spread


async def _claim(cache, keys) -> bool:
    """Take the shopper's 24 h budget just before sending, atomically. A held
    reminder can run more than once (the broker re-delivers a task whose ETA
    is past its visibility timeout), and one shopper can have two checkouts:
    whichever comes second finds the budget taken."""
    taken = []
    for key in keys:
        if not await cache.set_if_absent(
            key, "1", expire=NOTIFICATION_COOLDOWN_SECONDS
        ):
            for done in taken:
                await cache.delete(done)
            return False
        taken.append(key)
    return True


async def _ordered_since(
    session, store_id, since, *, customer_id=None, phone=None
) -> bool:
    """Rule 1: the shopper placed an order at or after ``since``, the
    checkout's last activity. Attribution to the row can miss (a different
    phone spelling, an order made in the hub), so this asks the orders."""
    from sqlalchemy import or_, select

    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.database.models.tenant.order import OrderModel

    who = []
    if customer_id is not None:
        who.append(OrderModel.customer_id == customer_id)
    if phone:
        who.append(CustomerModel.phone.in_((phone, _e164(phone))))
    if not who:
        return False
    found = await session.execute(
        select(OrderModel.id)
        .join(CustomerModel, CustomerModel.id == OrderModel.customer_id)
        .where(
            OrderModel.store_id == store_id,
            OrderModel.created_at >= since,
            or_(*who),
        )
        .limit(1)
    )
    return found.first() is not None


async def _log_whatsapp(session, store, phone: str, checkout_id, result) -> None:
    """Into message_logs, like every other template send: the WhatsApp
    dashboard shows it and it counts against the store's paid allowance."""
    from src.core.interfaces.services.messaging_service import (
        EGYPTIAN_TEMPLATES,
        MessageType,
    )
    from src.infrastructure.events.handlers.whatsapp_notification_handler import (
        _persist_message_log,
    )

    await _persist_message_log(
        session,
        tenant_id=store.tenant_id,
        store_id=store.id,
        phone=phone,
        template_name=EGYPTIAN_TEMPLATES[MessageType.ABANDONED_CART]["en"].name,
        message_id=result.message_id,
        status_str=str(getattr(result.status, "value", result.status)),
        metadata={"checkout_id": str(checkout_id), "source": "abandoned_cart_auto"},
    )


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

    # ── Pass 1: anonymous abandoned checkouts that carry a phone ────────
    # The Redis scan below only sees `cart:customer:*`, so every anonymous
    # cart was invisible to recovery. The identity layer now attaches a phone
    # to abandoned_checkouts rows BEFORE any customer exists (typed at the
    # OTP prompt or the save-cart nudge), which makes those rows recoverable.
    # Swept BEFORE the customer carts are queued: a shopper who signed in
    # mid-checkout owns such a row and a customer cart, and the queued
    # reminder has to see what this pass just sent.
    try:
        anon_stats = await _sweep_anonymous_phone_checkouts(
            cache, threshold=threshold, max_age=max_age
        )
        stats["anon_candidates"] = anon_stats["candidates"]
        stats["anon_notified"] = anon_stats["notified"]
        stats["anon_held"] = anon_stats["held"]
        stats["anon_skipped"] = anon_stats["skipped"]
        stats["errors"] += anon_stats["errors"]
    except Exception:
        logger.exception("anonymous_abandoned_checkout_sweep_failed")
        stats["errors"] += 1

    # ── Pass 2: logged-in customers' carts ──────────────────────────────
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

            if cache:
                # Reminded in the last 24h (the sender spends this budget
                # only when something goes out, so a store with reminders
                # off never blocks the merchant's manual nudge).
                cooldown_key = NOTIFICATION_COOLDOWN_KEY.format(
                    store_id=store_id, customer_id=customer_id
                )
                if await cache.exists(cooldown_key):
                    stats["skipped"] += 1
                    continue
                # Each cart state is handed to the sender once. The budget
                # was the only guard before, so a cart left alone was queued
                # again each time it expired: three reminders in 72 h.
                # ponytail: one attempt per cart state, so a failed send or a
                # switch turned on later waits for the cart to change; release
                # the claim in the sender if that loses reminders.
                if not await cache.set_if_absent(
                    f"abandoned_cart_queued:{store_id}:{customer_id}:{updated_at_str}",
                    "1",
                    expire=ABANDONED_CART_MAX_AGE_HOURS * 3600,
                ):
                    stats["skipped"] += 1
                    continue

            send_abandoned_cart_notification_task.delay(
                customer_id=customer_id, store_id=store_id
            )
            stats["notified"] += 1

        except Exception as e:
            logger.warning(f"Error processing cart key {key}: {e}")
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

    These rows have ``customer_id IS NULL``, so the customer-cart scan can
    never find them. The phone arrived via the
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
    within 24h. The rest are the module docstring's rules; the merchant's
    switch gates the nudge, not the hub notification.
    """
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.abandoned_checkout import (
        AbandonedCheckoutModel,
    )
    from src.infrastructure.database.models.tenant.store import StoreModel

    stats = {"candidates": 0, "notified": 0, "held": 0, "skipped": 0, "errors": 0}

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
                    # cycle, and _reminded() makes re-selection harmless.
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

        for row in rows:
            try:
                store = stores_by_id.get(row.store_id)
                if store is None or not store.subdomain:
                    stats["skipped"] += 1
                    continue
                # A reminder held for the morning owns this checkout until then.
                if cache and await cache.exists(_held_key(row.id)):
                    stats["skipped"] += 1
                    continue
                if await _ordered_since(
                    session, row.store_id, row.last_activity_at, phone=str(row.phone)
                ):
                    stats["skipped"] += 1
                    continue

                # Feed row for the hub bell — before the WhatsApp gate, so a
                # merchant with nudges off still sees the abandoned cart.
                # dedupe on the checkout row: one notification per cart.
                noticed = await emit_notification_standalone(
                    store_id=row.store_id,
                    category="abandoned_carts",
                    kind="cart.abandoned",
                    data={
                        "customer_name": None,
                        "phone": str(row.phone),
                        "items_count": sum(
                            (li.get("quantity") or 0) for li in (row.line_items or [])
                        ),
                        "total_cents": int(row.total or 0),
                        "currency": row.currency or "EGP",
                        "checkout_id": str(row.id),
                    },
                    link="/orders/abandoned",
                    entity_type="abandoned_checkout",
                    entity_id=row.id,
                    dedupe_key=_checkout_dedupe_key(row.id),
                )
                if noticed.written:
                    await _publish_abandoned(
                        store_id=row.store_id,
                        checkout_id=row.id,
                        items_count=sum(
                            (li.get("quantity") or 0) for li in (row.line_items or [])
                        ),
                        total_cents=int(row.total or 0),
                        currency=row.currency or "EGP",
                    )
                outcome = await _remind_anonymous(session, cache, store, row)
                if outcome == "sent":
                    stats["notified"] += 1
                elif outcome == "held":
                    stats["held"] += 1
                else:
                    stats["skipped"] += 1
            except Exception:
                stats["errors"] += 1
                logger.exception(
                    "anon_abandoned_nudge_row_failed",
                    extra={"checkout_id": str(getattr(row, "id", None))},
                )

    return stats


async def _remind_anonymous(session, cache, store, row) -> str:
    """Rules 3-7 and the WhatsApp send for one anonymous checkout. The sweep
    runs it by day; at night it queues the held task, which runs it again in
    the morning. Returns what happened."""
    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service
    from src.infrastructure.messaging.tasks.whatsapp_scheduled_send_dispatcher import (
        _resolve_language,
    )
    from src.infrastructure.repositories.whatsapp_opt_in_repository import (
        WhatsAppOptInRepository,
    )

    if not await _recovery_included(session, store):
        return "not_in_plan"
    if not _reminders_on(store) or _reminded(row):
        return "off_or_reminded"
    morning = _quiet_until(store)
    if morning is not None:
        if cache:
            # Keeps the sweep off this checkout until the held task has run.
            hold = (morning - _utcnow()).total_seconds() + 3600
            await cache.set(_held_key(row.id), "1", expire=int(hold))
        send_held_abandoned_reminder_task.apply_async(
            kwargs={"checkout_id": str(row.id)}, eta=morning
        )
        return "held"

    phone = _e164(str(row.phone))
    if await WhatsAppOptInRepository(session).has_opt_out(row.store_id, phone):
        return "opted_out"
    # Shared cooldown with stores/abandoned_checkouts.notify_whatsapp — the
    # merchant's manual nudge and this auto one are one budget, not two.
    budget = [phone_cooldown_key(row.store_id, phone)]
    if cache and not await _claim(cache, budget):
        return "notified_recently"

    sent = False
    try:
        # Transport nuance: GOWA renders the nudge locally; the Meta path uses
        # the approved abandoned_cart template and a failed send (e.g. not
        # approved for this store) is simply logged below.
        service = await get_whatsapp_service(row.store_id, session, store.tenant_id)
        # The merchant's "send in which language" choice, as the order
        # notifications and the manual nudge honour it.
        language = _resolve_language(store.settings or {}, store.default_language)
        result = await service.send_abandoned_cart(
            MessageRecipient(phone=phone, language=language),
            store.name,
            cart_token=f"{store.subdomain}/{row.id}",
        )
        if not result.success:
            logger.info(
                "anon_abandoned_nudge_send_failed",
                extra={
                    "checkout_id": str(row.id),
                    "store_id": str(row.store_id),
                    "error_code": result.error_code,
                },
            )
            return "send_failed"
        sent = True
        # Merchant-visible marker on the row itself.
        extra = dict(row.extra_data or {})
        extra["wa_auto_nudge_at"] = datetime.now(UTC).isoformat()
        row.extra_data = extra
        await session.commit()
        await _log_whatsapp(session, store, phone, row.id, result)
        return "sent"
    finally:
        if cache and not sent:  # nothing went out: give the budget back
            for key in budget:
                await cache.delete(key)


@celery_app.task(
    name="tasks.send_held_abandoned_reminder",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def send_held_abandoned_reminder_task(self, checkout_id: str):
    """An anonymous checkout's reminder, held through the night (rule 7)."""
    try:
        return run_async(_send_held_reminder(checkout_id))
    except Exception as exc:
        logger.exception("held_abandoned_reminder_failed")
        raise self.retry(exc=exc)


async def _send_held_reminder(checkout_id: str) -> dict:
    """Hours have passed since the sweep held it, so check everything again."""
    from uuid import UUID

    from src.infrastructure.cache.redis_cache import RedisCacheService
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.abandoned_checkout import (
        AbandonedCheckoutModel,
    )
    from src.infrastructure.database.models.tenant.store import StoreModel

    async with AsyncSessionLocal() as session:
        row = await session.get(AbandonedCheckoutModel, UUID(checkout_id))
        if row is None or row.recovered_at is not None:
            return {"outcome": "recovered"}
        if await _ordered_since(
            session, row.store_id, row.last_activity_at, phone=str(row.phone)
        ):
            return {"outcome": "converted"}
        store = await session.get(StoreModel, row.store_id)
        cache = RedisCacheService() if settings.redis_host else None
        try:
            return {"outcome": await _remind_anonymous(session, cache, store, row)}
        finally:
            if cache:
                await cache.close()


async def _publish_abandoned(**fields) -> None:
    """``checkout.abandoned``, once per cart the merchant is told about: the
    notification's dedupe key is the event's. Awaited, not published on the
    bus, so it is sent before the task's loop stops."""
    from src.core.events.commerce_events import CheckoutAbandonedEvent
    from src.infrastructure.events.handlers.webhook_handler import (
        handle_webhook_checkout_abandoned,
    )

    try:
        await handle_webhook_checkout_abandoned(CheckoutAbandonedEvent(**fields))
    except Exception:
        logger.exception("checkout_abandoned_webhook_failed")


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
    cart_items_count: int = 0,
    cart_subtotal: int = 0,
    cart_currency: str = "EGP",
):
    """Send abandoned cart recovery notification via WhatsApp + email fallback.

    The ``cart_*`` arguments are unused (the checkout row carries the cart);
    they stay so that messages queued before that change still run.
    """
    try:
        result = run_async(
            _send_notification(customer_id=customer_id, store_id=store_id)
        )
        logger.info(
            f"Abandoned cart notification sent: customer={customer_id}, "
            f"store={store_id}, channel={result.get('channel')}"
        )
        return result
    except Exception as exc:
        logger.error(f"Abandoned cart notification failed: {exc}")
        raise self.retry(exc=exc)


async def _send_notification(customer_id: str, store_id: str) -> dict:
    """Send recovery notification via WhatsApp (primary) or email (fallback).

    The checkout is the customer's newest open ``abandoned_checkouts`` row:
    the one the merchant sees and the anonymous pass would remind, so the two
    passes share one notification, one webhook and one reminder per checkout.
    At night this task re-queues itself for the morning (rule 7), and the
    morning run checks everything again.
    """
    from uuid import UUID

    from sqlalchemy import or_, select

    from src.application.services.meta_capi_purchase_dispatcher import (
        is_real_email,
    )
    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.cache.redis_cache import RedisCacheService
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.abandoned_checkout import (
        AbandonedCheckoutModel,
    )
    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service
    from src.infrastructure.messaging.tasks.whatsapp_scheduled_send_dispatcher import (
        _resolve_language,
    )
    from src.infrastructure.repositories.whatsapp_opt_in_repository import (
        WhatsAppOptInRepository,
    )

    async with AsyncSessionLocal() as session:
        customer = (
            await session.execute(
                select(CustomerModel).where(CustomerModel.id == UUID(customer_id))
            )
        ).scalar_one_or_none()
        if not customer:
            return {"sent": False, "reason": "customer_not_found"}

        store = (
            await session.execute(
                select(StoreModel).where(StoreModel.id == UUID(store_id))
            )
        ).scalar_one_or_none()
        if not store:
            return {"sent": False, "reason": "store_not_found"}

        # A row started before the shopper signed in has no customer_id, but
        # cart/track writes the customer's email and phone onto it.
        who = [
            AbandonedCheckoutModel.customer_id == customer.id,
            AbandonedCheckoutModel.email == customer.email,
        ]
        if customer.phone:
            who.append(AbandonedCheckoutModel.phone == customer.phone)
        oldest = datetime.now(UTC) - timedelta(hours=ABANDONED_CART_MAX_AGE_HOURS)
        checkout = (
            await session.execute(
                select(AbandonedCheckoutModel)
                .where(
                    AbandonedCheckoutModel.store_id == store.id,
                    AbandonedCheckoutModel.recovered_at.is_(None),
                    AbandonedCheckoutModel.last_activity_at >= oldest,
                    or_(*who),
                )
                .order_by(AbandonedCheckoutModel.last_activity_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if checkout is None:
            return {"sent": False, "reason": "no_open_checkout"}
        phone = customer.phone or checkout.phone
        if await _ordered_since(
            session,
            store.id,
            checkout.last_activity_at,
            customer_id=customer.id,
            phone=phone,
        ):
            return {"sent": False, "reason": "converted"}

        real_name = f"{customer.first_name or ''} {customer.last_name or ''}".strip()
        items_count = sum(
            (li.get("quantity") or 0) for li in (checkout.line_items or [])
        )
        total_cents = int(checkout.total or 0)
        currency = checkout.currency or "EGP"

        # Merchant notification feed (hub bell) — written regardless of
        # whether a customer nudge goes out, because "a cart was abandoned"
        # is merchant-actionable on its own. One row per checkout.
        noticed = await emit_notification_standalone(
            store_id=store.id,
            category="abandoned_carts",
            kind="cart.abandoned",
            data={
                "customer_name": real_name or None,
                "customer_id": str(customer.id),
                "items_count": items_count,
                "total_cents": total_cents,
                "currency": currency,
                "checkout_id": str(checkout.id),
            },
            link="/orders/abandoned",
            entity_type="abandoned_checkout",
            entity_id=checkout.id,
            dedupe_key=_checkout_dedupe_key(checkout.id),
        )
        if noticed.written:
            await _publish_abandoned(
                store_id=store.id,
                checkout_id=checkout.id,
                customer_id=customer.id,
                items_count=items_count,
                total_cents=total_cents,
                currency=currency,
            )

        if not await _recovery_included(session, store):
            return {"sent": False, "reason": "not_in_plan"}
        if not _reminders_on(store):
            return {"sent": False, "reason": "reminders_off"}
        if _reminded(checkout):
            return {"sent": False, "reason": "already_reminded"}
        morning = _quiet_until(store)
        if morning is not None:
            send_abandoned_cart_notification_task.apply_async(
                kwargs={"customer_id": customer_id, "store_id": store_id},
                eta=morning,
            )
            return {"sent": False, "reason": "held_until_morning"}

        budget = [
            NOTIFICATION_COOLDOWN_KEY.format(store_id=store.id, customer_id=customer.id)
        ]
        if phone:
            budget.append(phone_cooldown_key(store.id, phone))
        cache = RedisCacheService() if settings.redis_host else None
        claimed = False
        channel = None
        try:
            if cache:
                claimed = await _claim(cache, budget)
                if not claimed:
                    return {"sent": False, "reason": "notified_recently"}

            if phone and not await WhatsAppOptInRepository(session).has_opt_out(
                store.id, _e164(phone)
            ):
                try:
                    language = _resolve_language(
                        store.settings or {}, store.default_language
                    )
                    service = await get_whatsapp_service(
                        store.id, session, store.tenant_id
                    )
                    result = await service.send_abandoned_cart(
                        MessageRecipient(
                            phone=_e164(phone),
                            name=real_name or ("عميلنا" if language == "ar" else None),
                            language=language,
                        ),
                        store.name,
                        # `<subdomain>/<checkout_id>`: the apex /cart/ redirector
                        # forwards to the storefront's /api/cart/recover, which
                        # restores this checkout into the shopper's session.
                        cart_token=(
                            f"{store.subdomain}/{checkout.id}"
                            if store.subdomain
                            else ""
                        ),
                    )
                    if result.success:
                        channel = "whatsapp"
                        await _log_whatsapp(
                            session, store, _e164(phone), checkout.id, result
                        )
                    else:
                        logger.warning(
                            f"WhatsApp abandoned cart failed: {result.error_message}"
                        )
                except Exception as e:
                    logger.warning(f"WhatsApp abandoned cart error: {e}")

            # Fallback only: a shopper WhatsApp reached gets no second message.
            if (
                channel is None
                and customer.accepts_marketing
                and is_real_email(customer.email)
            ):
                try:
                    await _send_recovery_email(
                        store, checkout, str(customer.email), real_name or None
                    )
                    channel = "email"
                except Exception as e:
                    logger.warning(f"Email abandoned cart error: {e}")

            if channel is None:
                return {"sent": False, "reason": "no_channel"}

            # Never raise after a send: the task would retry and send again.
            try:
                if channel == "email":
                    checkout.recovery_email_sent_at = datetime.now(UTC)
                else:
                    checkout.extra_data = {
                        **(checkout.extra_data or {}),
                        "wa_auto_nudge_at": datetime.now(UTC).isoformat(),
                    }
                await session.commit()
            except Exception:
                logger.exception("abandoned_cart_reminder_bookkeeping_failed")
        finally:
            if cache:
                if claimed and channel is None:  # nothing went out: give it back
                    for key in budget:
                        await cache.delete(key)
                await cache.close()

    return {
        "sent": True,
        "channel": channel,
        "customer_id": customer_id,
        "store_id": store_id,
        "checkout_id": str(checkout.id),
    }


async def _send_recovery_email(store, checkout, to: str, name: str | None) -> None:
    """The branded recovery email, linking back to this exact checkout."""
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.external_services.resend.email_templates.abandoned_cart import (  # noqa: E501
        abandoned_cart_email_html,
    )

    # Store.store_url's rule: a custom-domain store links to its own domain.
    base = (
        f"https://{store.custom_domain}"
        if store.custom_domain
        else f"https://{store.subdomain or store.slug}.numueg.app"
    )
    subject, html = abandoned_cart_email_html(
        store_name=store.name,
        recovery_url=f"{base}/api/cart/recover?cart={checkout.id}",
        line_items=list(checkout.line_items or []),
        total_cents=int(checkout.total or 0),
        currency=checkout.currency or "EGP",
        customer_name=name,
        # The shopper's language, not the merchant's dashboard language.
        language="en" if (store.default_language or "ar").startswith("en") else "ar",
        logo_url=store.logo_url,
    )
    await ResendEmailService().send_email(
        EmailMessage(to=to, subject=subject, html_content=html)
    )
