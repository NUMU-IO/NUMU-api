"""Back in Stock app end to end on the test database (BIS-U-03, U-07, I-01 …
I-17, S-02, S-03, S-06, S-12). Only the outside world is stubbed: WhatsApp,
email and the Celery queue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select

from src.api.dependencies.app_session import AppSession
from src.api.v1.routes.apps import back_in_stock as merchant
from src.api.v1.routes.storefront import apps_back_in_stock as shop
from src.application.services import back_in_stock as bis
from src.application.services import back_in_stock_whatsapp as wa
from src.application.services.numu_apps import PURGERS
from src.core.entities.product import ProductStatus
from src.infrastructure.database.models.tenant.back_in_stock import (
    BackInStockSettingsModel,
)
from src.infrastructure.database.models.tenant.back_in_stock import (
    BackInStockWaiterModel as Waiter,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.variant import VariantModel
from src.infrastructure.events.handlers import back_in_stock_handler as handler
from src.infrastructure.messaging.tasks import back_in_stock_app_tasks as tasks
from src.infrastructure.repositories import back_in_stock_repository as repo
from tests.unit import app_platform_rows as rows
from tests.unit.test_app_install_gate import _Session

PHONE = "+201012345678"


@pytest.fixture
def world(monkeypatch, test_session):
    """A store with the app live, a T-shirt (S in stock, M sold out) and the
    outside world stubbed."""
    for module in (tasks, handler):
        monkeypatch.setattr(module, "AsyncSessionLocal", lambda: _Session(test_session))
        monkeypatch.setattr(module, "enable_rls_bypass", AsyncMock())
    monkeypatch.setattr(shop, "whatsapp_usable", AsyncMock(return_value=True))
    monkeypatch.setattr(wa, "whatsapp_usable", AsyncMock(return_value=True))
    queued = MagicMock()
    monkeypatch.setattr(tasks.send_task, "apply_async", queued)
    monkeypatch.setattr(tasks.restock_check_task, "apply_async", MagicMock())
    monkeypatch.setattr(tasks.attribution_task, "apply_async", MagicMock())

    the_app = []

    async def build(*, live=True):
        owner = await rows.user(test_session)
        store = await rows.store(test_session, owner)
        if not the_app:
            the_app.append(await rows.app(test_session, slug="back-in-stock"))
        made = the_app[0]
        if live:
            await rows.install(test_session, store, made.app)
        product = ProductModel(
            id=uuid4(),
            tenant_id=store.tenant_id,
            store_id=store.id,
            name="تيشيرت قطن",
            slug="tshirt",
            status=ProductStatus.ACTIVE,
            quantity=0,
        )
        test_session.add(product)
        small, medium = (
            VariantModel(
                id=uuid4(),
                tenant_id=store.tenant_id,
                store_id=store.id,
                product_id=product.id,
                position=i,
                option_values={"المقاس": size},
                price_amount=25000,
                price_currency="EGP",
                inventory_quantity=qty,
                track_inventory=True,
            )
            for i, (size, qty) in enumerate((("S", 4), ("M", 0)))
        )
        test_session.add_all([small, medium])
        await test_session.flush()
        return SimpleNamespace(
            store=store,
            owner=owner,
            product=product,
            small=small,
            medium=medium,
            session=AppSession(
                store_id=store.id, user_id=owner.id, app_id=made.app.id, locale="ar"
            ),
        )

    return SimpleNamespace(build=build, db=test_session, queued=queued)


def _status(out):
    if isinstance(out, JSONResponse):
        import json

        return out.status_code, json.loads(out.body)
    return 200, out


async def _subscribe(w, x, **body):
    data = {
        "product_id": x.product.id,
        "variant_id": x.medium.id,
        "phone": "01012345678",
        **body,
    }
    return _status(
        await shop.subscribe(x.store.id, shop.SubscribeRequest(**data), w.db)
    )


async def _waiters(db, store_id):
    return (
        (await db.execute(select(Waiter).where(Waiter.store_id == store_id)))
        .scalars()
        .all()
    )


# ─── Shopper routes ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscribe_paths(world):
    """BIS-I-02."""
    x = await world.build()

    assert await _subscribe(world, x) == (200, {"status": "subscribed"})
    assert await _subscribe(world, x, phone="+20 101 234 5678") == (
        200,
        {"status": "already"},
    )
    assert len(await _waiters(world.db, x.store.id)) == 1
    assert await _subscribe(world, x, variant_id=x.small.id) == (
        200,
        {"status": "available"},
    )
    assert len(await _waiters(world.db, x.store.id)) == 1
    with pytest.raises(HTTPException) as e:
        await _subscribe(world, x, product_id=uuid4())
    assert e.value.status_code == 404

    waiter = (await _waiters(world.db, x.store.id))[0]
    assert (waiter.contact, waiter.channel, waiter.variant_title) == (
        PHONE,
        "whatsapp",
        "M",
    )
    assert len(waiter.link_token) == 22 and waiter.link_token != waiter.unsub_token


@pytest.mark.asyncio
async def test_caps(world):
    """BIS-I-03: the 11th wait for one contact, and a store over its cap."""
    x = await world.build()
    for _ in range(10):
        world.db.add(
            Waiter(
                store_id=x.store.id,
                tenant_id=x.store.tenant_id,
                product_id=uuid4(),
                channel="whatsapp",
                contact=PHONE,
                status="waiting",
                product_title="p",
                link_token=bis.new_token(),
                unsub_token=bis.new_token(),
            )
        )
    await world.db.flush()
    assert (await _subscribe(world, x))[0] == 429

    world.db.add(
        BackInStockSettingsModel(
            store_id=x.store.id,
            tenant_id=x.store.tenant_id,
            contact="phone_or_email",
            signup_cap=10,
            wa_cap=200,
            email_cap=1000,
        )
    )
    await world.db.flush()
    assert (await _subscribe(world, x, phone=None, email="new@example.com"))[0] == 429
    assert len(await _waiters(world.db, x.store.id)) == 10  # nothing written


@pytest.mark.asyncio
async def test_phone_without_whatsapp_is_refused_and_email_accepted(world, monkeypatch):
    """BIS-I-04."""
    x = await world.build()
    monkeypatch.setattr(shop, "whatsapp_usable", AsyncMock(return_value=False))

    code, body = await _subscribe(world, x)
    assert (code, body["code"]) == (409, "channel_unavailable")
    assert await _subscribe(world, x, phone=None, email="Mona@Example.com") == (
        200,
        {"status": "subscribed"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "usable", "offered"),
    [
        ("phone_or_email", True, "phone_or_email"),
        ("phone_or_email", False, "email"),
        ("phone", True, "phone"),
        ("phone", False, None),
        ("email", False, "email"),
    ],
)
async def test_config_never_offers_a_phone_while_whatsapp_cannot_send(
    world, monkeypatch, setting, usable, offered
):
    """The widget would only meet a 409; ``None`` hides it."""
    x = await world.build()
    await merchant.put_settings(
        x.session,
        world.db,
        merchant.SettingsIn(contact=setting, signup_cap=50, wa_cap=20, email_cap=30),
    )
    monkeypatch.setattr(shop, "whatsapp_usable", AsyncMock(return_value=usable))
    assert await shop.config(x.store.id, world.db) == {"contact": offered}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "body", "want"),
    [
        ("phone", {"phone": None, "email": "a@b.co"}, (422, "phone_only")),
        ("email", {}, (422, "email_only")),
        ("phone_or_email", {"phone": "0225551234"}, (422, "invalid_phone")),
        ("phone_or_email", {"phone": None, "email": "nope"}, (422, "invalid_email")),
        ("phone_or_email", {"email": "a@b.co"}, (422, "one_contact")),
        ("phone_or_email", {"phone": None}, (422, "contact_required")),
    ],
)
async def test_bodies_follow_the_contact_setting(world, setting, body, want):
    """BIS-U-03 / S-07: every invalid body → 422 and nothing written."""
    x = await world.build()
    world.db.add(
        BackInStockSettingsModel(
            store_id=x.store.id,
            tenant_id=x.store.tenant_id,
            contact=setting,
            signup_cap=300,
            wa_cap=200,
            email_cap=1000,
        )
    )
    await world.db.flush()
    code, out = await _subscribe(world, x, **body)
    assert (code, out["code"]) == want
    assert await _waiters(world.db, x.store.id) == []


@pytest.mark.asyncio
async def test_the_honeypot_looks_like_success_and_writes_nothing(world):
    x = await world.build()
    assert await _subscribe(world, x, website="http://spam") == (
        200,
        {"status": "subscribed"},
    )
    assert await _waiters(world.db, x.store.id) == []


@pytest.mark.asyncio
async def test_without_the_app_every_shopper_route_says_not_installed(world):
    """BIS-S-03."""
    x = await world.build(live=False)
    for call in (
        shop.config(x.store.id, world.db),
        _subscribe(world, x),
    ):
        with pytest.raises(HTTPException) as e:
            await call
        assert (e.value.status_code, e.value.detail) == (404, "not installed")


@pytest.mark.asyncio
async def test_unsubscribe_one_contact_in_one_store(world):
    """BIS-I-10 / S-12."""
    x, y = await world.build(), await world.build()
    await _subscribe(world, x)
    await _subscribe(world, x, variant_id=None, product_id=x.product.id)
    await _subscribe(world, y)
    token = (await _waiters(world.db, x.store.id))[0].unsub_token

    info = await shop.unsubscribe_info(x.store.id, token, world.db)
    assert info["contact"] == "+20 10•• ••• 5678"
    with pytest.raises(HTTPException):
        await shop.unsubscribe_info(
            y.store.id, token, world.db
        )  # another store's token
    await shop.unsubscribe(x.store.id, token, world.db)

    assert {w.status for w in await _waiters(world.db, x.store.id)} == {"unsubscribed"}
    assert {w.status for w in await _waiters(world.db, y.store.id)} == {"waiting"}


@pytest.mark.asyncio
async def test_buy_now_link(world):
    """BIS-I-09 / S-12: first click stored once; the Location is the store's
    own host from the database."""
    x = await world.build()
    await _subscribe(world, x)
    waiter = (await _waiters(world.db, x.store.id))[0]
    sub = x.store.subdomain

    url = await shop.resolve_link(world.db, f"{sub}/{waiter.link_token}")
    assert url == (
        f"https://{sub}.numueg.app/products/tshirt?variant={x.medium.id}"
        "&utm_source=numu_back_in_stock&utm_medium=whatsapp"
    )
    first = waiter.clicked_at
    assert first is not None
    await shop.resolve_link(world.db, f"{sub}/{waiter.link_token}")
    assert waiter.clicked_at == first

    assert (
        await shop.resolve_link(world.db, f"{sub}/bad-token")
        == f"https://{sub}.numueg.app/"
    )
    assert (
        await shop.resolve_link(world.db, f"no-such-store/{waiter.link_token}") is None
    )
    assert await shop.resolve_link(world.db, f"{sub}/u/{waiter.unsub_token}") == (
        f"https://{sub}.numueg.app/unsubscribe/back-in-stock/{waiter.unsub_token}"
    )


# ─── Restock, sends, caps ────────────────────────────────────────────


async def _restock(world, x, qty):
    x.medium.inventory_quantity = qty
    await world.db.flush()
    return await tasks.restock_check(x.store.id, x.product.id)


@pytest.mark.asyncio
async def test_restock_queues_up_to_the_cap_and_paces_the_sends(world):
    """BIS-I-05 / U-06: 25 waiters, 2 units → cap 20, paced ≥ 30 s apart start."""
    x = await world.build()
    for i in range(25):
        await _subscribe(world, x, phone=f"0101234{i:04d}")

    assert await _restock(world, x, 2) == {"queued": 20}
    countdowns = [c.kwargs["countdown"] for c in world.queued.call_args_list]
    assert len(countdowns) == 20 and countdowns[0] >= 30
    assert all(
        b - a >= bis.SEND_GAP_SECONDS for a, b in zip(countdowns, countdowns[1:])
    )


@pytest.mark.asyncio
async def test_queued_at_is_the_due_time_so_the_sweep_never_cuts_a_paced_send(world):
    """A big restock paces sends past an hour; the sweep must count "stuck"
    from each row's due time, not from when the check ran."""
    x = await world.build()
    for i in range(3):
        await _subscribe(world, x, phone=f"0101234{i:04d}")
    before = datetime.now(UTC)
    await _restock(world, x, 5)

    countdowns = [c.kwargs["countdown"] for c in world.queued.call_args_list]
    due = sorted(
        w.queued_at.replace(tzinfo=UTC)  # SQLite hands back naive datetimes
        for w in await _waiters(world.db, x.store.id)
    )
    for when, countdown in zip(due, countdowns, strict=True):
        assert (
            timedelta(seconds=countdown)
            <= when - before
            < timedelta(seconds=countdown + 60)
        )

    # Due in two hours (a long paced queue): the sweep leaves it alone.
    late = (await _waiters(world.db, x.store.id))[0]
    late.queued_at = datetime.now(UTC) + timedelta(hours=2)
    await world.db.flush()
    await tasks.sweep()
    assert late.status == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("templates", "locale", "sent_in"),
    [
        ({"ar": "APPROVED", "en": "PENDING"}, "en", "ar"),
        ({"ar": "PENDING", "en": "APPROVED"}, "ar", "en"),
        ({"ar": "APPROVED", "en": "APPROVED"}, "en", "en"),
    ],
)
async def test_the_alert_goes_out_in_an_approved_language(
    world, monkeypatch, templates, locale, sent_in
):
    """can_send() needs one approved language; a shopper whose own language
    is still pending gets the other one rather than failing for good."""
    from src.infrastructure.events.handlers import whatsapp_notification_handler

    x = await world.build()
    await _subscribe(world, x, locale=locale)
    waiter = (await _waiters(world.db, x.store.id))[0]
    state = {
        "access": True,
        "credentials_ok": True,
        "needs_approved_template": True,
        "template": templates,
        "template_category": dict.fromkeys(templates, "UTILITY"),
    }
    monkeypatch.setattr(wa, "whatsapp_state", AsyncMock(return_value=state))
    monkeypatch.setattr(
        wa,
        "WhatsAppOptInRepository",
        lambda db: SimpleNamespace(has_opt_out=AsyncMock(return_value=False)),
    )
    service = SimpleNamespace(
        send_message=AsyncMock(
            return_value=SimpleNamespace(
                success=True, message_id="wamid.9", error_code=None
            )
        )
    )
    monkeypatch.setattr(wa, "get_whatsapp_service", AsyncMock(return_value=service))
    monkeypatch.setattr(
        whatsapp_notification_handler, "_persist_message_log", AsyncMock()
    )

    assert await wa.send_alert(world.db, x.store, waiter) == ("wamid.9", None)
    sent = service.send_message.await_args.args[0]
    assert sent.recipient.language == sent_in


@pytest.mark.asyncio
async def test_two_checks_at_once_queue_each_waiter_once(world):
    """BIS-I-13."""
    x = await world.build()
    for i in range(3):
        await _subscribe(world, x, phone=f"0101234{i:04d}")
    x.medium.inventory_quantity = 5
    await world.db.flush()

    first = await tasks.restock_check(x.store.id, x.product.id)
    second = await tasks.restock_check(x.store.id, x.product.id)

    assert (first, second) == ({"queued": 3}, {"queued": 0})


@pytest.mark.asyncio
async def test_the_daily_cap_rolls_the_rest_to_tomorrow(world):
    """BIS-I-15: 250 due, wa_cap 200 → 200 today, 50 the next day."""
    x = await world.build()
    world.db.add(
        BackInStockSettingsModel(
            store_id=x.store.id,
            tenant_id=x.store.tenant_id,
            contact="phone_or_email",
            signup_cap=1000,
            wa_cap=200,
            email_cap=1000,
        )
    )
    for i in range(250):
        world.db.add(
            Waiter(
                store_id=x.store.id,
                tenant_id=x.store.tenant_id,
                product_id=x.product.id,
                variant_id=x.medium.id,
                channel="whatsapp",
                contact=f"+2010{i:08d}",
                status="waiting",
                product_title="p",
                link_token=bis.new_token(),
                unsub_token=bis.new_token(),
            )
        )
    await world.db.flush()

    assert await _restock(world, x, 30) == {"queued": 200}
    yesterday = datetime.now(UTC) - timedelta(days=1)
    for w in await _waiters(world.db, x.store.id):
        if w.status == "queued":
            w.status, w.queued_at, w.notified_at = "notified", yesterday, yesterday
    await world.db.flush()
    assert await tasks.restock_check(x.store.id, x.product.id) == {"queued": 50}


@pytest.mark.asyncio
async def test_whatsapp_waiters_wait_while_whatsapp_cannot_send(world, monkeypatch):
    x = await world.build()
    await _subscribe(world, x)
    await _subscribe(world, x, phone=None, email="m@example.com")
    monkeypatch.setattr(wa, "whatsapp_usable", AsyncMock(return_value=False))

    assert await _restock(world, x, 3) == {"queued": 1}
    assert {(w.channel, w.status) for w in await _waiters(world.db, x.store.id)} == {
        ("whatsapp", "waiting"),
        ("email", "queued"),
    }


async def _queued_waiter(world, x, **body):
    await _subscribe(world, x, **body)
    await _restock(world, x, 3)
    return (await _waiters(world.db, x.store.id))[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "final", "want"),
    [
        (("wamid.1", None), True, ("notified", "wamid.1", None)),
        ((None, "opt_out"), False, ("failed", None, "opt_out")),  # guard: no retry
        (
            (None, "131026"),
            True,
            ("failed", None, "131026"),
        ),  # last try of a provider error
    ],
)
async def test_send_results(world, monkeypatch, result, final, want):
    """BIS-I-07."""
    x = await world.build()
    waiter = await _queued_waiter(world, x)
    monkeypatch.setattr(wa, "send_alert", AsyncMock(return_value=result))

    await tasks.send(waiter.id, final_try=final)

    assert (waiter.status, waiter.message_id, waiter.fail_reason) == want


@pytest.mark.asyncio
async def test_a_provider_error_is_retried_before_it_fails(world, monkeypatch):
    x = await world.build()
    waiter = await _queued_waiter(world, x)
    monkeypatch.setattr(wa, "send_alert", AsyncMock(return_value=(None, "131026")))

    with pytest.raises(RuntimeError):
        await tasks.send(waiter.id, final_try=False)
    assert waiter.status == "queued"


def _record_bypass_and_commits(world, monkeypatch):
    """The order of RLS-bypass calls and commits. The bypass is
    transaction-local, so after a commit it must be set again before the
    session reads or writes tenant rows."""
    calls = []
    tasks.enable_rls_bypass.side_effect = lambda db: calls.append("bypass")
    commit = world.db.commit

    async def recording_commit():
        calls.append("commit")
        await commit()

    monkeypatch.setattr(world.db, "commit", recording_commit)
    return calls


@pytest.mark.asyncio
async def test_send_sets_the_bypass_again_after_the_message_log_commit(
    world, monkeypatch
):
    x = await world.build()
    waiter = await _queued_waiter(world, x)
    calls = _record_bypass_and_commits(world, monkeypatch)

    async def send_alert(db, store, w):
        await db.commit()  # core's _persist_message_log commits
        return "wamid.1", None

    monkeypatch.setattr(wa, "send_alert", send_alert)
    await tasks.send(waiter.id)

    assert calls == ["bypass", "commit", "bypass", "commit"]
    assert waiter.status == "notified"


@pytest.mark.asyncio
async def test_an_unsubscribe_stops_an_alert_already_queued(world, monkeypatch):
    x = await world.build()
    waiter = await _queued_waiter(world, x)
    assert waiter.status == "queued"
    send_alert = AsyncMock(return_value=("wamid.1", None))
    monkeypatch.setattr(wa, "send_alert", send_alert)

    await shop.unsubscribe(x.store.id, waiter.unsub_token, world.db)

    assert await tasks.send(waiter.id) == {"skipped": "not_queued"}
    assert waiter.status == "unsubscribed"
    send_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_sold_out_again_goes_back_in_line(world):
    x = await world.build()
    waiter = await _queued_waiter(world, x)
    x.medium.inventory_quantity = 0
    await world.db.flush()

    assert await tasks.send(waiter.id) == {"skipped": "sold_out_again"}
    assert waiter.status == "waiting"


@pytest.mark.asyncio
async def test_email_alert(world, monkeypatch):
    """BIS-I-08: sender name = store name; one-click unsubscribe headers on
    the store's own host."""
    from src.infrastructure.external_services.resend import email_service

    x = await world.build()
    waiter = await _queued_waiter(world, x, phone=None, email="m@example.com")
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(
        email_service.ResendEmailService, "__init__", lambda self, *a, **k: None
    )
    monkeypatch.setattr(email_service.ResendEmailService, "send_email", sent)

    await tasks.send(waiter.id)

    message = sent.await_args.args[0]
    host = f"{x.store.subdomain}.numueg.app"
    assert (message.to, message.from_name) == ("m@example.com", x.store.name)
    assert message.headers == {
        "List-Unsubscribe": f"<https://{host}/unsubscribe/back-in-stock/{waiter.unsub_token}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    assert "٢٥٠ ج.م" in message.html_content
    assert waiter.status == "notified"


# ─── Events, attribution, retention, sweep ───────────────────────────


@pytest.mark.asyncio
async def test_stock_event_schedules_a_check_only_when_someone_waits(world):
    x = await world.build()
    event = SimpleNamespace(
        store_id=x.store.id, product_id=x.product.id, variant_id=x.medium.id
    )

    await handler.on_inventory_changed(event)
    tasks.restock_check_task.apply_async.assert_not_called()
    await _subscribe(world, x)
    await handler.on_inventory_changed(event)
    assert tasks.restock_check_task.apply_async.call_args.kwargs["countdown"] == 60


@pytest.mark.asyncio
async def test_handlers_do_nothing_without_the_app(world):
    """BIS-S-03."""
    x = await world.build(live=False)
    world.db.add(
        Waiter(
            store_id=x.store.id,
            tenant_id=x.store.tenant_id,
            product_id=x.product.id,
            channel="email",
            contact="m@example.com",
            status="waiting",
            product_title="p",
            link_token=bis.new_token(),
            unsub_token=bis.new_token(),
        )
    )
    await world.db.flush()

    await handler.on_inventory_changed(
        SimpleNamespace(store_id=x.store.id, product_id=x.product.id)
    )
    await handler.on_product_deleted(
        SimpleNamespace(store_id=x.store.id, product_id=x.product.id)
    )
    assert await tasks.restock_check(x.store.id, x.product.id) == {
        "skipped": "not_live"
    }

    tasks.restock_check_task.apply_async.assert_not_called()
    assert (await _waiters(world.db, x.store.id))[0].status == "waiting"


@pytest.mark.asyncio
async def test_product_deleted_closes_its_waiters(world):
    """BIS-I-12."""
    x = await world.build()
    await _subscribe(world, x)

    await handler.on_product_deleted(
        SimpleNamespace(store_id=x.store.id, product_id=x.product.id)
    )

    assert (await _waiters(world.db, x.store.id))[0].status == "closed"


@pytest.mark.asyncio
async def test_attribution(world):
    """BIS-I-11: an order from the alerted shopper with the variant counts."""
    x = await world.build()
    await _subscribe(world, x)
    waiter = (await _waiters(world.db, x.store.id))[0]
    waiter.status, waiter.notified_at = (
        "notified",
        datetime.now(UTC) - timedelta(days=2),
    )
    customer = CustomerModel(
        id=uuid4(),
        tenant_id=x.store.tenant_id,
        store_id=x.store.id,
        email="c@example.com",
        phone="01012345678",
        first_name="Mona",
        last_name="S",
    )
    world.db.add(customer)

    def order(variant):
        o = OrderModel(
            id=uuid4(),
            tenant_id=x.store.tenant_id,
            store_id=x.store.id,
            customer_id=customer.id,
            order_number=f"ORD-{uuid4().hex[:6]}",
            status="pending",
            total=25000,
            created_at=datetime.now(
                UTC
            ),  # Postgres returns aware times; SQLite would not
            shipping_address={},
            line_items=[
                {
                    "product_id": str(x.product.id),
                    "variant_id": str(variant),
                    "quantity": 1,
                    "unit_price": 25000,
                    "total_price": 25000,
                }
            ],
        )
        world.db.add(o)
        return o

    miss = order(x.small.id)
    await world.db.flush()
    assert await tasks.attribution(miss.id) == {"matched": 0}
    hit = order(x.medium.id)
    await world.db.flush()
    assert await tasks.attribution(hit.id) == {"matched": 1}
    assert (waiter.order_id, waiter.revenue) == (hit.id, 25000)


@pytest.mark.asyncio
async def test_retention_task(world):
    """BIS-I-14."""
    x = await world.build()
    now = datetime.now(UTC)

    def row(status, created_days, changed_days):
        w = Waiter(
            store_id=x.store.id,
            tenant_id=x.store.tenant_id,
            product_id=x.product.id,
            channel="email",
            contact=f"{uuid4().hex[:6]}@example.com",
            status=status,
            product_title="p",
            link_token=bis.new_token(),
            unsub_token=bis.new_token(),
            created_at=now - timedelta(days=created_days),
            updated_at=now - timedelta(days=changed_days),
        )
        world.db.add(w)
        return w

    old_wait, fresh_wait = row("waiting", 181, 181), row("waiting", 5, 5)
    old_sent, new_sent = row("notified", 60, 31), row("notified", 20, 10)
    await world.db.flush()

    assert await tasks.retention(now) == {"closed": 1, "erased": 1}
    assert (old_wait.status, fresh_wait.status) == ("closed", "waiting")
    assert (old_sent.contact, new_sent.contact is not None) == (None, True)


@pytest.mark.asyncio
async def test_sweep_requeues_lost_sends_and_checks_every_product(world, monkeypatch):
    """BIS-I-06: stock raised with no event still gets a check."""
    x = await world.build()
    await _subscribe(world, x)
    lost = (await _waiters(world.db, x.store.id))[0]
    lost.status, lost.queued_at = "queued", datetime.now(UTC) - timedelta(hours=2)
    await world.db.flush()

    calls = _record_bypass_and_commits(world, monkeypatch)
    assert await tasks.sweep() == {"checked": 1}
    assert calls == ["bypass", "commit", "bypass"]
    assert lost.status == "waiting"
    tasks.restock_check_task.apply_async.assert_called_once_with(
        args=[str(x.store.id), str(x.product.id)]
    )


# ─── Merchant routes ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_defaults_on_first_open(world, monkeypatch):
    """BIS-I-01."""
    x = await world.build()
    state = {
        "mode": "shared",
        "transport": "meta",
        "access": True,
        "access_reason": None,
        "credentials_ok": True,
        "needs_approved_template": True,
        "template": {"ar": "PENDING"},
        "template_category": {"ar": "UTILITY"},
    }
    monkeypatch.setattr(merchant, "whatsapp_state", AsyncMock(return_value=state))

    out = await merchant.status(x.session, world.db)

    assert out["settings"] == bis.DEFAULT_SETTINGS
    assert out["whatsapp"]["usable"] is False  # template still waiting for Meta
    assert out["theme"]["ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "usable"),
    [
        (
            {
                "access": True,
                "credentials_ok": True,
                "needs_approved_template": True,
                "template": {"ar": "APPROVED"},
            },
            True,
        ),
        (
            {
                "access": True,
                "credentials_ok": True,
                "needs_approved_template": False,
                "template": {},
            },
            True,
        ),  # GOWA
        (
            {
                "access": False,
                "credentials_ok": True,
                "needs_approved_template": True,
                "template": {"ar": "APPROVED"},
            },
            False,
        ),
        (
            {
                "access": True,
                "credentials_ok": False,
                "needs_approved_template": True,
                "template": {"ar": "APPROVED"},
            },
            False,
        ),
    ],
)
def test_whatsapp_states(state, usable):
    """BIS-I-16 (decision): own Meta, GOWA, no access, bad credentials."""
    assert wa.can_send(state) is usable


@pytest.mark.asyncio
async def test_merchant_routes_stay_in_the_tokens_store(world):
    """BIS-I-17 / S-02."""
    x, y = await world.build(), await world.build()
    await _subscribe(world, x)
    await _subscribe(world, y)
    theirs = (await _waiters(world.db, y.store.id))[0]
    mine = (await _waiters(world.db, x.store.id))[0]

    with pytest.raises(HTTPException) as e:
        await merchant.delete_waiter(x.session, world.db, theirs.id)
    assert e.value.status_code == 404
    assert (await merchant.delete_waiter(x.session, world.db, mine.id))[
        "deleted"
    ] == str(mine.id)
    assert await _waiters(world.db, x.store.id) == []
    assert len(await _waiters(world.db, y.store.id)) == 1

    overview = await merchant.overview(y.session, world.db)
    assert overview["waiting"] == 1
    listed = await merchant.waiters(y.session, world.db, y.product.id)
    assert listed["items"][0]["contact"] == "+20 10•• ••• 5678"


@pytest.mark.asyncio
async def test_notify_now_needs_stock(world):
    x = await world.build()
    with pytest.raises(HTTPException) as e:
        await merchant.notify_now(
            x.session,
            world.db,
            merchant.NotifyNow(product_id=x.product.id, variant_id=x.medium.id),
        )
    assert e.value.status_code == 409
    await merchant.notify_now(
        x.session,
        world.db,
        merchant.NotifyNow(product_id=x.product.id, variant_id=x.small.id),
    )
    tasks.restock_check_task.apply_async.assert_called_once()


@pytest.mark.asyncio
async def test_settings_validate_and_save(world):
    x = await world.build()
    with pytest.raises(ValidationError):
        merchant.SettingsIn(contact="fax", signup_cap=10, wa_cap=10, email_cap=10)
    with pytest.raises(ValidationError):
        merchant.SettingsIn(contact="phone", signup_cap=0, wa_cap=10, email_cap=10)

    saved = await merchant.put_settings(
        x.session,
        world.db,
        merchant.SettingsIn(contact="email", signup_cap=50, wa_cap=20, email_cap=30),
    )
    assert saved == {
        "contact": "email",
        "signup_cap": 50,
        "wa_cap": 20,
        "email_cap": 30,
    }
    assert (await shop.config(x.store.id, world.db)) == {"contact": "email"}


@pytest.mark.asyncio
async def test_the_test_send_goes_to_the_merchants_own_contact(world, monkeypatch):
    x = await world.build()
    x.owner.phone = "01098765432"
    await world.db.flush()
    sent = AsyncMock(return_value=("wamid.t", None))
    monkeypatch.setattr(wa, "send_alert", sent)

    out = await merchant.test_send(
        x.session,
        world.db,
        merchant.TestSend(channel="whatsapp", product_id=x.product.id),
    )

    assert out == {"sent": True, "to": "+20 10•• ••• 5432", "reason": None}
    assert sent.await_args.args[2].contact == "+201098765432"


# ─── Purge, data rights, messages ────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_leaves_nothing(world):
    """BIS-S-06."""
    x = await world.build()
    await _subscribe(world, x)
    await merchant.put_settings(
        x.session,
        world.db,
        merchant.SettingsIn(contact="phone", signup_cap=5, wa_cap=5, email_cap=5),
    )

    await PURGERS["back-in-stock"](world.db, x.store.id)

    assert await _waiters(world.db, x.store.id) == []
    assert await world.db.get(BackInStockSettingsModel, x.store.id) is None


@pytest.mark.asyncio
async def test_data_rights_export_and_delete(world):
    """BIS-S-06: matched by the shopper's phone or email, one store only."""
    x = await world.build()
    await _subscribe(world, x)
    await _subscribe(world, x, phone=None, email="mona@example.com")
    await _subscribe(world, x, phone="01111111111")
    shopper = SimpleNamespace(phone="+20 10 1234 5678", email="Mona@Example.com")
    contacts = repo.shopper_contacts(shopper)

    exported = await repo.export_for(world.db, x.store.id, contacts)
    assert {r["contact"] for r in exported} == {PHONE, "mona@example.com"}
    assert await repo.delete_for(world.db, x.store.id, contacts) == 2
    assert [w.contact for w in await _waiters(world.db, x.store.id)] == [
        "+201111111111"
    ]


def test_message_builders():
    """BIS-U-07."""
    from src.core.interfaces.services.messaging_service import MessageType
    from src.core.whatsapp_plain_render import render_plain_template
    from src.core.whatsapp_rich_templates import RICH_TEMPLATES

    waiter = SimpleNamespace(
        product_title="تيشيرت قطن",
        variant_title="أسود / L",
        link_token="L" * 22,
        unsub_token="U" * 22,
        variant_id=None,
        locale="ar",
        channel="email",
        contact="m@example.com",
    )
    params = bis.template_params("متجر القاهرة", "cairo", waiter)
    assert all(params.values())
    assert params["order_link"] == "back-in-stock/cairo/" + "L" * 22
    assert params["stop_link"] == "back-in-stock/cairo/u/" + "U" * 22
    for tmpl in (t for t in RICH_TEMPLATES if t["name"] == bis.TEMPLATE_NAME):
        assert not tmpl["body"].startswith("{{") and not tmpl["body"].endswith("}}")
        assert [b["url"] for b in tmpl["buttons"]] == ["https://numueg.app/a/{{1}}"] * 2
    text = render_plain_template(MessageType.BACK_IN_STOCK_ALERT, "ar", params).text
    assert "https://numueg.app/a/back-in-stock/cairo/" + "L" * 22 in text

    store = SimpleNamespace(
        name="متجر القاهرة", custom_domain=None, subdomain="cairo", logo_url=None
    )
    email = bis.email_message(
        store=store, waiter=waiter, product_slug="tshirt", image=None, price="٢٥٠ ج.م"
    )
    assert email["subject"] == "تيشيرت قطن — أسود / L رجع تاني في متجر القاهرة"
    assert (
        '<bdi dir="ltr">٢٥٠ ج.م</bdi>' in email["html"] and 'dir="rtl"' in email["html"]
    )
    assert bis.price_text(125000, "EGP", "en") == "EGP 1,250"
    assert bis.price_text(125050, "EGP", "ar") == "١٬٢٥٠٫٥٠ ج.م"
