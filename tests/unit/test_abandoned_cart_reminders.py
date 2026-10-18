"""Abandoned-cart reminders follow the rules in abandoned_cart_tasks' docstring.

One beat run is ``_detect_and_notify`` followed by the reminders it queued,
on the test database. Only the outside world is stubbed: Meta, Resend, Redis,
the Celery queue, the hub feed and the webhook.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from src.api.v1.routes.storefront import cart as cart_routes
from src.api.v1.routes.storefront import cart_sdk_aliases as cart_aliases
from src.api.v1.routes.storefront._cart_owner import CartOwner
from src.api.v1.routes.stores import abandoned_checkouts as checkout_routes
from src.application.services.whatsapp_entitlement import count_messages
from src.core.entities.cart import Cart
from src.core.entities.product import ProductStatus
from src.core.events.order_events import OrderCreatedEvent
from src.core.interfaces.services.messaging_service import (
    EGYPTIAN_TEMPLATES,
    MessageResult,
    MessageStatus,
    MessageType,
)
from src.core.value_objects.cart_item import CartItem
from src.infrastructure.cache import redis_cache
from src.infrastructure.database import connection
from src.infrastructure.database.models.tenant.abandoned_checkout import (
    AbandonedCheckoutModel,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.message_log import MessageLogModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.whatsapp_opt_in import (
    WhatsAppOptInModel,
)
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)
from src.infrastructure.events.handlers import abandoned_recovery_handler as recovery
from src.infrastructure.external_services import whatsapp
from src.infrastructure.external_services.resend import email_service
from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
)
from src.infrastructure.messaging.tasks import abandoned_cart_tasks as tasks
from src.infrastructure.repositories import cart_repository
from src.infrastructure.repositories.abandoned_checkout_repository import (
    AbandonedCheckoutRepository,
)
from tests.unit import app_platform_rows as rows
from tests.unit.test_app_install_gate import _Session

PHONE = "+201012345678"
LOCAL = "01012345678"  # the same number, as a checkout form may store it
CAIRO = ZoneInfo("Africa/Cairo")
DAY = datetime(2026, 10, 18, 12, 30, tzinfo=CAIRO).astimezone(UTC)
NIGHT = datetime(2026, 10, 19, 0, 30, tzinfo=CAIRO).astimezone(UTC)


class Redis:
    """RedisCacheService and the raw client the cart scan reads, as dicts."""

    def __init__(self):
        self.keys: dict = {}
        self.carts: dict = {}

    async def exists(self, key):
        return key in self.keys

    async def set(self, key, value, expire=None):
        self.keys[key] = value
        return True

    async def set_if_absent(self, key, value, expire=None):
        if key in self.keys:
            return False
        self.keys[key] = value
        return True

    async def delete(self, key):
        return self.keys.pop(key, None) is not None

    async def close(self):
        pass

    async def scan_iter(self, match):
        for key in list(self.carts):
            yield key

    async def get(self, key):
        return self.carts.get(key)


@pytest.fixture
def world(monkeypatch, test_session):
    w = SimpleNamespace(db=test_session, redis=Redis(), queued=[], notes=[], hooks=[])
    w.whatsapp, w.transports, w.emails, w.held = [], [], [], []
    # Daytime in Cairo unless a test says otherwise: quiet hours (rule 7)
    # must not depend on when the suite runs.
    monkeypatch.setattr(tasks, "_utcnow", lambda: DAY)
    for task in (
        tasks.send_abandoned_cart_notification_task,
        tasks.send_held_abandoned_reminder_task,
    ):  # held for the morning: kwargs + eta
        monkeypatch.setattr(task, "apply_async", lambda **kw: w.held.append(kw))
    monkeypatch.setattr(connection, "AsyncSessionLocal", lambda: _Session(w.db))
    monkeypatch.setattr(redis_cache, "RedisCacheService", lambda *a, **k: w.redis)
    monkeypatch.setattr(
        cart_repository,
        "RedisCartRepository",
        lambda: SimpleNamespace(
            _get_client=AsyncMock(return_value=w.redis), close=AsyncMock()
        ),
    )
    monkeypatch.setattr(tasks.settings, "redis_host", "redis")
    monkeypatch.setattr(tasks.settings, "whatsapp_enabled", True)
    monkeypatch.setattr(tasks, "_recovery_included", AsyncMock(return_value=True))
    monkeypatch.setattr(
        tasks.send_abandoned_cart_notification_task,
        "delay",
        lambda **kw: w.queued.append(kw),
    )

    async def emit(**kw):  # the feed's unique (store_id, dedupe_key)
        key = (kw["store_id"], kw["dedupe_key"])
        written = key not in w.notes
        if written:
            w.notes.append(key)
        return SimpleNamespace(written=written)

    async def publish(**fields):
        w.hooks.append(fields)

    async def send_message(service, content):  # Meta, never reached
        w.whatsapp.append(content)
        return MessageResult(
            success=True, message_id=f"wamid.{uuid4().hex}", status=MessageStatus.SENT
        )

    async def transport(store_id, session, tenant_id=None):
        w.transports.append(store_id)
        return WhatsAppMessagingService()

    class Resend:
        async def send_email(self, message):
            w.emails.append(message)
            return True

    monkeypatch.setattr(tasks, "emit_notification_standalone", emit)
    monkeypatch.setattr(tasks, "_publish_abandoned", publish)
    monkeypatch.setattr(WhatsAppMessagingService, "send_message", send_message)
    monkeypatch.setattr(whatsapp, "get_whatsapp_service", transport)
    monkeypatch.setattr(email_service, "ResendEmailService", Resend)
    return w


async def _store(w, *, reminders=True, **settings):
    store = await rows.store(w.db, await rows.user(w.db))
    store.default_language = "ar"
    store.settings = {"whatsapp_notifications": {"abandoned_cart": reminders}}
    store.settings.update(settings)
    await w.db.flush()
    return store


async def _customer(w, store, *, phone=PHONE, email=None, accepts_marketing=False):
    customer = CustomerModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        # What phone-first checkout gives a customer who typed no email.
        email=email or f"guest+{uuid4().hex[:12]}@noemail.numueg.app",
        first_name="Mona",
        last_name="Saad",
        phone=phone,
        accepts_marketing=accepts_marketing,
    )
    w.db.add(customer)
    await w.db.flush()
    return customer


async def _checkout(w, store, *, customer=None, phone=PHONE, hours_ago=2, **fields):
    at = datetime.now(UTC) - timedelta(hours=hours_ago)
    row = AbandonedCheckoutModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        customer_id=customer.id if customer else None,
        email=customer.email if customer else None,
        phone=phone,
        line_items=[
            {"product_id": str(uuid4()), "product_name": "فستان", "quantity": 2}
        ],
        subtotal=100_000,
        total=100_000,
        currency="EGP",
        last_activity_at=at,
        created_at=at,
        updated_at=at,
        extra_data={},
    )
    for name, value in fields.items():
        setattr(row, name, value)
    w.db.add(row)
    await w.db.flush()
    return row


def _cart(w, store, customer, *, hours_ago=2):
    """The signed-in shopper's live cart in Redis."""
    w.redis.carts[f"cart:customer:{store.id}:{customer.id}"] = json.dumps({
        "customer_id": str(customer.id),
        "store_id": str(store.id),
        "items": [{"product_id": str(uuid4()), "quantity": 2, "unit_price": 50_000}],
        "currency": "EGP",
        "updated_at": (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(),
    })


async def _beat(w):
    """One run of the 30-minute beat, then the reminders it queued."""
    await tasks._detect_and_notify()
    queued, w.queued = w.queued, []
    for kwargs in queued:
        await tasks._send_notification(**kwargs)


async def _morning(w, monkeypatch):
    """Run what was held for the morning, each at its ETA."""
    held, w.held = w.held, []
    for call in held:
        monkeypatch.setattr(tasks, "_utcnow", lambda eta=call["eta"]: eta)
        if "checkout_id" in call["kwargs"]:
            await tasks._send_held_reminder(**call["kwargs"])
        else:
            await tasks._send_notification(**call["kwargs"])


async def _order(w, store, customer):
    order = OrderModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        customer_id=customer.id,
        order_number=f"ORD-{uuid4().hex[:6]}",
        status="pending",
        total=100_000,
        created_at=datetime.now(UTC),
        shipping_address={},
        line_items=[],
    )
    w.db.add(order)
    await w.db.flush()
    return order


def _sent_to(w):
    return sorted(c.recipient.phone for c in w.whatsapp)


async def test_a_signed_in_cart_is_reminded_once_through_the_stores_whatsapp(world):
    """The import resolves; the store's own transport sends (its paid access
    and allowance apply there), in the merchant's chosen language; the link
    restores the checkout; the send is logged against the allowance."""
    w = world
    store = await _store(w, whatsapp={"message_language": "en"})
    customer = await _customer(w, store)
    checkout = await _checkout(w, store, customer=customer)
    _cart(w, store, customer)
    anonymous = await _checkout(w, store, phone="+201112223334")

    await _beat(w)

    assert w.transports == [store.id, store.id]
    assert _sent_to(w) == sorted([PHONE, "+201112223334"])
    assert {c.recipient.language for c in w.whatsapp} == {"en"}
    links = {c.template_params["cart_token"] for c in w.whatsapp}
    assert links == {
        f"{store.subdomain}/{checkout.id}",
        f"{store.subdomain}/{anonymous.id}",
    }
    assert checkout.extra_data["wa_auto_nudge_at"]
    since = datetime.now(UTC) - timedelta(hours=1)
    assert await count_messages(w.db, store.id, since) == 2
    assert len(w.notes) == 2 and len(w.hooks) == 2
    assert w.emails == []

    await _beat(w)
    assert len(w.whatsapp) == 2


@pytest.mark.parametrize(("in_plan", "reminders_on"), [(True, False), (False, True)])
async def test_reminders_off_or_out_of_plan_reach_no_shopper_on_any_channel(
    world, monkeypatch, in_plan, reminders_on
):
    """The email fallback used to ignore the merchant's switch."""
    w = world
    monkeypatch.setattr(tasks, "_recovery_included", AsyncMock(return_value=in_plan))
    store = await _store(w, reminders=reminders_on)
    customer = await _customer(w, store, email="mona@gmail.com", accepts_marketing=True)
    await _checkout(w, store, customer=customer)
    _cart(w, store, customer)
    await _checkout(w, store, phone="+201112223334")

    await _beat(w)

    assert w.whatsapp == [] and w.emails == []
    # The merchant still hears about each cart, once.
    assert len(w.notes) == 2 and len(w.hooks) == 2
    # Nothing went out, so the merchant's own nudge is not blocked for a day.
    assert not [k for k in w.redis.keys if k.startswith("abandoned_cart_notified:")]


async def test_email_is_only_a_fallback_for_customers_who_accepted_marketing(world):
    w = world
    store = await _store(w)
    consented = await _customer(
        w, store, phone=None, email="mona@gmail.com", accepts_marketing=True
    )
    silent = await _customer(w, store, phone=None, email="sara@gmail.com")
    placeholder = await _customer(w, store, phone=None, accepts_marketing=True)
    checkouts = {}
    for customer in (consented, silent, placeholder):
        checkouts[customer.id] = await _checkout(
            w, store, customer=customer, phone=None
        )
        _cart(w, store, customer)

    await _beat(w)

    assert w.whatsapp == []
    [mail] = w.emails
    assert mail.to == "mona@gmail.com"
    assert f"/api/cart/recover?cart={checkouts[consented.id].id}" in mail.html_content
    assert checkouts[consented.id].recovery_email_sent_at is not None


async def test_a_shopper_who_opted_out_of_whatsapp_gets_no_whatsapp(world):
    """Opt-outs are stored as E.164; the checkout row may hold ``01…``."""
    w = world
    store = await _store(w)
    now = datetime.now(UTC)
    w.db.add(
        WhatsAppOptInModel(
            id=uuid4(),
            tenant_id=store.tenant_id,
            store_id=store.id,
            phone=PHONE,
            source="checkout",
            opted_in_at=now - timedelta(days=9),
            opted_out_at=now - timedelta(days=3),
            opt_out_reason="inbound_stop_keyword",
        )
    )
    customer = await _customer(w, store)
    await _checkout(w, store, customer=customer)
    _cart(w, store, customer)
    await _checkout(w, store, phone=LOCAL)

    await _beat(w)

    assert w.whatsapp == [] and w.emails == []


async def test_only_checkouts_active_in_the_last_72_hours_are_reminded(world):
    """The first run after months broken must not reach back further."""
    w = world
    store = await _store(w)
    expected = []
    for n, hours in enumerate((2, 70, 74, 24 * 90)):
        customer = await _customer(w, store, phone=f"+2010100000{n}0")
        await _checkout(w, store, customer=customer, hours_ago=hours)
        _cart(w, store, customer, hours_ago=hours)
        await _checkout(w, store, phone=f"+2010100000{n}1", hours_ago=hours)
        if hours < 72:
            expected += [customer.phone, f"+2010100000{n}1"]

    await _beat(w)

    assert _sent_to(w) == sorted(expected)


async def test_a_checkout_that_became_an_order_is_neither_reminded_nor_announced(
    world,
):
    """The order was never attributed back to her rows (recovered_at is
    empty): it still counts."""
    w = world
    store = await _store(w)
    customer = await _customer(w, store, email="mona@gmail.com", accepts_marketing=True)
    await _checkout(w, store, customer=customer)
    _cart(w, store, customer)
    await _checkout(w, store, phone=LOCAL)  # before she signed in
    await _order(w, store, customer)

    await _beat(w)

    assert w.whatsapp == [] and w.emails == []
    assert w.notes == [] and w.hooks == []


async def test_a_checkout_is_reminded_once_after_the_24h_budget_runs_out(world):
    w = world
    store = await _store(w)
    customer = await _customer(w, store)
    await _checkout(w, store, customer=customer)
    _cart(w, store, customer)
    await _checkout(w, store, phone="+201112223334")
    await _beat(w)
    assert len(w.whatsapp) == 2

    # A day on: the budget expired, both checkouts are still open and inside
    # the 72 h window.
    for key in [k for k in w.redis.keys if k.startswith("abandoned_cart_notified:")]:
        del w.redis.keys[key]
    await _beat(w)

    assert len(w.whatsapp) == 2


async def test_a_checkout_the_merchant_already_reminded_is_left_alone(world):
    w = world
    store = await _store(w)
    await _checkout(
        w, store, extra_data={"last_whatsapp_notified_at": "2026-10-18T09:00:00"}
    )
    await _checkout(
        w,
        store,
        phone="+201112223334",
        recovery_email_sent_at=datetime.now(UTC) - timedelta(minutes=50),
    )

    await _beat(w)

    assert w.whatsapp == []


async def test_one_shopper_with_an_anonymous_row_and_a_signed_in_cart_hears_once(
    world,
):
    """She typed her phone at checkout (row created anonymously, ``01…``),
    then signed in: one WhatsApp, one feed row, one webhook."""
    w = world
    store = await _store(w)
    customer = await _customer(w, store, email="mona@gmail.com")
    # cart/track writes the signed-in customer's email onto her row.
    await _checkout(w, store, phone=LOCAL, email=customer.email)
    _cart(w, store, customer)

    await _beat(w)

    assert _sent_to(w) == [PHONE]
    assert len(w.notes) == 1 and len(w.hooks) == 1


def test_every_spelling_of_a_phone_shares_one_24h_budget():
    store_id = uuid4()
    assert tasks.phone_cooldown_key(store_id, LOCAL) == tasks.phone_cooldown_key(
        store_id, PHONE
    )


async def test_the_reminder_link_restores_the_cart_without_doubling_it(
    world, monkeypatch
):
    """A signed-in shopper taps the link in the browser that still holds the
    cart: the restore must not add every line a second time."""
    w = world
    store = await _store(w)
    customer = await _customer(w, store)
    product_id = uuid4()
    checkout = await _checkout(
        w,
        store,
        customer=customer,
        line_items=[{"product_id": str(product_id), "quantity": 2}],
    )
    live = Cart(
        id=uuid4(),
        session_id=str(uuid4()),
        store_id=store.id,
        customer_id=customer.id,
        currency="EGP",
    )
    live.add_item(
        CartItem(
            product_id=product_id, product_name="فستان", quantity=2, unit_price=500
        )
    )
    carts = SimpleNamespace(
        get_by_customer_id=AsyncMock(return_value=live), save=AsyncMock()
    )
    monkeypatch.setattr(cart_routes, "_cart_repo", carts)
    monkeypatch.setattr(cart_aliases, "_cart_repo", carts)
    monkeypatch.setattr(cart_aliases, "_build_cart_response", AsyncMock())
    product = SimpleNamespace(
        name="فستان",
        status=ProductStatus.ACTIVE,
        store_id=store.id,
        is_in_stock=True,
        effective_price=lambda: SimpleNamespace(cents=500),
        sku=None,
        images=[],
    )

    for _ in range(2):  # and tapping it twice changes nothing either
        await cart_aliases.sdk_recover_cart(
            cart_aliases.SdkRecoverCartRequest(recover_id=checkout.id),
            CartOwner(
                customer_id=customer.id,
                session_id=None,
                store_id=store.id,
                is_guest=False,
            ),
            SimpleNamespace(get_by_id=AsyncMock(return_value=product)),
            w.db,
        )

    assert live.get_item(product_id).quantity == 2


async def test_a_night_time_abandonment_waits_for_9am_store_time(world, monkeypatch):
    """Rule 7: held until 09:00 Cairo plus at most 30 minutes, the merchant
    still hears now, and the morning run sends."""
    w = world
    monkeypatch.setattr(tasks, "_utcnow", lambda: NIGHT)
    store = await _store(w)
    customer = await _customer(w, store)
    await _checkout(w, store, customer=customer)
    _cart(w, store, customer)
    await _checkout(w, store, phone="+201112223334")

    await _beat(w)
    await _beat(w)  # the rest of the night queues nothing more

    assert w.whatsapp == [] and len(w.notes) == 2
    nine = datetime(2026, 10, 19, 9, tzinfo=CAIRO)
    assert len(w.held) == 2
    assert all(nine <= c["eta"] <= nine + timedelta(minutes=30) for c in w.held)

    await _morning(w, monkeypatch)

    assert _sent_to(w) == sorted([PHONE, "+201112223334"])


async def test_quiet_hours_follow_the_stores_own_clock(world, monkeypatch):
    """00:30 in Cairo is 17:30 in New York: that store's shopper hears now."""
    w = world
    monkeypatch.setattr(tasks, "_utcnow", lambda: NIGHT)
    cairo = await _store(w)
    new_york = await _store(w, timezone="America/New_York")
    await _checkout(w, cairo, phone="+201112223334")
    await _checkout(w, new_york, phone=PHONE)

    await _beat(w)

    assert _sent_to(w) == [PHONE]
    [held] = w.held
    assert held["eta"].astimezone(CAIRO).hour == 9


@pytest.mark.parametrize("overnight", ["ordered", "switched_off"])
async def test_a_held_reminder_is_checked_again_in_the_morning(
    world, monkeypatch, overnight
):
    w = world
    monkeypatch.setattr(tasks, "_utcnow", lambda: NIGHT)
    store = await _store(w)
    customer = await _customer(w, store)
    await _checkout(w, store, customer=customer)
    _cart(w, store, customer)
    await _checkout(w, store, phone="+201112223334")
    await _beat(w)
    assert len(w.held) == 2

    if overnight == "ordered":  # both order before 09:00, unattributed
        await _order(w, store, customer)
        await _order(w, store, await _customer(w, store, phone="+201112223334"))
    else:
        store.settings = {"whatsapp_notifications": {"abandoned_cart": False}}
        await w.db.flush()
    await _morning(w, monkeypatch)

    assert w.whatsapp == [] and w.emails == []


async def test_the_manual_nudge_checks_the_template_it_sends(world):
    """It looked up abandoned_cart_v2 while v3 went out: an approved v3 was
    refused, and an approved v2 let an unapproved v3 through."""
    w = world
    store = await _store(w, whatsapp={"provider": "meta_cloud"})
    checkout = await _checkout(w, store)
    sent_name = EGYPTIAN_TEMPLATES[MessageType.ABANDONED_CART]["ar"].name
    w.db.add(
        WhatsAppTemplateModel(
            id=uuid4(),
            tenant_id=store.tenant_id,
            store_id=store.id,
            name=sent_name,
            language="ar",
            category="MARKETING",
            status="APPROVED",
            body_text="-",
        )
    )
    await w.db.flush()

    out = await checkout_routes.notify_whatsapp(
        checkout.id, store, AbandonedCheckoutRepository(w.db), w.db
    )

    assert out.sent and len(w.whatsapp) == 1
    logged = await w.db.scalars(
        select(MessageLogModel.template_name).where(
            MessageLogModel.store_id == store.id
        )
    )
    assert logged.all() == [sent_name]


async def test_an_order_recovers_the_checkout_whatever_the_phone_spelling(
    world, monkeypatch
):
    """The row kept the phone as typed (01…); the customer has +201…."""
    w = world
    monkeypatch.setattr(recovery, "AsyncSessionLocal", lambda: _Session(w.db))
    store = await _store(w)
    customer = await _customer(w, store)
    checkout = await _checkout(w, store, phone=LOCAL)
    order = await _order(w, store, customer)

    await recovery.handle_order_created_recovery(
        OrderCreatedEvent(
            order_id=order.id,
            order_number=order.order_number,
            store_id=store.id,
            customer_id=customer.id,
            total=1000.0,
            currency="EGP",
        )
    )

    assert checkout.recovered_order_id == order.id
