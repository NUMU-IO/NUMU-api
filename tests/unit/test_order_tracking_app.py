"""Order Tracking app end to end on the test database (TRK-I-01 … I-12 on
SQLite, S-02, S-03, S-06, S-07, S-12, S-14, K-07). Only the outside world is
stubbed: WhatsApp, Bosta and the Celery queue. The real-Postgres runs of the
I-tests (locks, migrations, the sweep at scale) live in the local harness."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select

from src.api.dependencies.app_session import AppSession
from src.api.v1.routes.apps import order_tracking as merchant
from src.api.v1.routes.storefront import apps_order_tracking as shop
from src.application.services import back_in_stock_whatsapp as bisw
from src.application.services import order_tracking_service as svc
from src.core.entities.app import AppStatus
from src.core.entities.order import OrderStatus
from src.core.entities.order_activity import OrderActivityKind
from src.core.events.commerce_events import (
    ShipmentCreatedEvent,
    ShipmentStatusChangedEvent,
)
from src.core.events.order_events import OrderStatusChangedEvent
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppUninstallModel,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.order_activity import (
    OrderActivityModel,
)
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingMessageModel as Message,
)
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingParcelModel as ParcelRow,
)
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingRetryRequestModel as Retry,
)
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingSettingsModel as Settings,
)
from src.infrastructure.database.models.tenant.shipment import ShipmentModel
from src.infrastructure.database.models.tenant.shipping_zone import (
    ShippingZoneModel,
)
from src.infrastructure.events import commit_watch
from src.infrastructure.events.handlers import order_tracking_handler as handler
from src.infrastructure.messaging.tasks import order_tracking_tasks as tasks
from src.infrastructure.repositories import order_tracking_repository as repo
from tests.unit import app_platform_rows as rows
from tests.unit.test_app_install_gate import _Session
from tests.unit.test_back_in_stock_app import _send_lock

CAIRO = ZoneInfo("Africa/Cairo")
PHONE = "+201098433918"
STREET = "١٢ شارع التحرير"
EMAIL = "mona.shopper@example.com"
DESCRIPTION = "Delivery failed: carrier said 01098433918 refused"
TABLES = (ParcelRow, Retry, Message, Settings)


def _now() -> datetime:
    return datetime.now(UTC)


def _today() -> date:
    return _now().astimezone(CAIRO).date()


def history(*steps: str, end: datetime | None = None) -> list[dict]:
    """Shipment history entries one minute apart, the last at ``end``."""
    end = end or _now()
    out, prev = [], "created"
    for i, to in enumerate(steps):
        at = end - timedelta(minutes=len(steps) - 1 - i)
        description = DESCRIPTION if to == "failed" else f"carrier: {to}"
        out.append({
            "from": prev,
            "to": to,
            "description": description,
            "timestamp": at.isoformat(),
        })
        prev = to
    return out


@pytest.fixture
def world(monkeypatch, test_session):
    """Stores with the app live, and the outside world stubbed."""
    # The tests call the handlers themselves. Once an earlier test has built
    # the global bus, commit_watch would also fire them after every commit,
    # on this same shared session, while that commit is still running.
    monkeypatch.setattr(commit_watch, "_bus", None)
    for module in (tasks, handler):
        monkeypatch.setattr(module, "AsyncSessionLocal", lambda: _Session(test_session))
        monkeypatch.setattr(module, "enable_rls_bypass", AsyncMock())
    monkeypatch.setattr(tasks, "_sending", _send_lock(True))
    sends, reconciles = MagicMock(), MagicMock()
    monkeypatch.setattr(tasks.send_task, "apply_async", sends)
    monkeypatch.setattr(tasks.reconcile_task, "apply_async", reconciles)
    the_app: list = []

    async def build(*, live: bool = True, **install) -> SimpleNamespace:
        owner = await rows.user(test_session)
        store = await rows.store(test_session, owner)
        store.name = "Vionne"
        store.settings = {"timezone": "Africa/Cairo"}
        store.social_links = {"whatsapp": "0101 234 5678"}
        store.contact_phone = "0223456789"
        if not the_app:
            # Made from NUMU's partner account, like the real app, so the
            # Partner-apps kill switch applies to it.
            partner = await rows.user(test_session)
            the_app.append(
                await rows.app(test_session, slug=svc.SLUG, developer=partner)
            )
        made = the_app[0]
        if live:
            await rows.install(test_session, store, made.app, **install)
        customer = CustomerModel(
            id=uuid4(),
            tenant_id=store.tenant_id,
            store_id=store.id,
            email=EMAIL,
            phone=PHONE,
            first_name="منى",
            last_name="س",
        )
        test_session.add(customer)
        await test_session.flush()
        return SimpleNamespace(
            store=store,
            owner=owner,
            customer=customer,
            app=made.app,
            session=AppSession(
                store_id=store.id, user_id=owner.id, app_id=made.app.id, locale="ar"
            ),
        )

    async def order(
        x,
        *,
        status=OrderStatus.SHIPPED,
        created_at: datetime | None = None,
        gov: str = "القاهرة",
        zone_days: tuple[int, int] | None = None,
        phone: str | None = "01098433918",
    ) -> OrderModel:
        zone_id = None
        if zone_days:
            zone = ShippingZoneModel(
                id=uuid4(),
                tenant_id=x.store.tenant_id,
                store_id=x.store.id,
                name="Cairo",
                estimated_days_min=zone_days[0],
                estimated_days_max=zone_days[1],
            )
            test_session.add(zone)
            zone_id = zone.id
        placed = created_at or _now() - timedelta(days=1)
        o = OrderModel(
            id=uuid4(),
            tenant_id=x.store.tenant_id,
            store_id=x.store.id,
            customer_id=x.customer.id,
            order_number=f"10{uuid4().int % 100000:05d}",
            status=status,
            total=125000,
            currency="EGP",
            shipping_zone_id=zone_id,
            shipping_address={
                "first_name": "منى",
                "last_name": "س",
                "address_line1": STREET,
                "city": "مدينة نصر",
                "state": gov,
                "country": "EG",
                "phone": phone,
            },
            line_items=[
                {
                    "product_id": str(uuid4()),
                    "product_name": "تيشيرت قطن",
                    "variant_name": "M",
                    "quantity": 1,
                    "unit_price": 125000,
                    "total_price": 125000,
                }
            ],
            extra_data={
                "status_history": [
                    {
                        "from": "pending",
                        "to": "confirmed",
                        "timestamp": (placed + timedelta(hours=1)).isoformat(),
                        "reason": None,
                    }
                ]
            },
            created_at=placed,
        )
        test_session.add(o)
        await test_session.flush()
        return o

    async def ship(
        x,
        o,
        *,
        status: str = "in_transit",
        steps: tuple[str, ...] = (),
        carrier: str = "bosta",
        end: datetime | None = None,
        kind: str = "forward",
        updated_at: datetime | None = None,
        **extra,
    ) -> ShipmentModel:
        s = ShipmentModel(
            id=uuid4(),
            tenant_id=x.store.tenant_id,
            store_id=x.store.id,
            order_id=o.id,
            carrier=carrier,
            tracking_number=f"BST{uuid4().hex[:6]}",
            status=status,
            shipment_type=kind,
            status_history=history(*steps, end=end) if steps else [],
            extra_data={},
            **extra,
        )
        if updated_at is not None:
            s.updated_at = updated_at
        test_session.add(s)
        await test_session.flush()
        return s

    return SimpleNamespace(
        build=build,
        order=order,
        ship=ship,
        db=test_session,
        sends=sends,
        reconciles=reconciles,
    )


def _created(s: ShipmentModel) -> ShipmentCreatedEvent:
    return ShipmentCreatedEvent(
        store_id=s.store_id,
        shipment_id=s.id,
        order_id=s.order_id,
        shipment_type=s.shipment_type,
        carrier=s.carrier,
        status=s.status,
    )


def _changed(s: ShipmentModel, previous: str) -> ShipmentStatusChangedEvent:
    return ShipmentStatusChangedEvent(
        store_id=s.store_id,
        shipment_id=s.id,
        order_id=s.order_id,
        shipment_type=s.shipment_type,
        carrier=s.carrier,
        status=s.status,
        previous_status=previous,
    )


def _order_event(o: OrderModel, new: str) -> OrderStatusChangedEvent:
    return OrderStatusChangedEvent(
        order_id=o.id,
        order_number=o.order_number,
        store_id=o.store_id,
        store_name="Vionne",
        customer_id=o.customer_id,
        previous_status="processing",
        new_status=new,
    )


async def _count(db, model, *where) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _body(out):
    if isinstance(out, JSONResponse):
        return out.status_code, json.loads(out.body)
    return 200, out


async def _view(world, x, o):
    response = Response()
    out = await shop.order_view(x.store.id, o.id, response, world.db)
    assert response.headers["Cache-Control"] == "private, no-store"
    return out


def _assert_private(payload) -> None:
    """TRK-S-12: no phone, street, email or carrier description."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    for secret in ("1098433918", STREET, EMAIL, "carrier said", "refused"):
        assert secret not in text, secret


# ─── TRK-I-01 / I-02: the handler's parcel rows ───────────────────────


async def test_a_forward_shipment_gets_one_parcel_row(world):
    """TRK-I-01."""
    x = await world.build()
    placed = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)  # 12:00 Cairo
    o = await world.order(x, zone_days=(2, 4), created_at=placed)
    s = await world.ship(x, o, status="created")

    await handler.on_shipment_created(_created(s))
    await handler.on_shipment_created(_created(s))  # a second event: nothing new

    assert await _count(world.db, ParcelRow, ParcelRow.store_id == x.store.id) == 1
    row = await repo.parcel_row(world.db, x.store.id, o.id)
    assert (row.governorate_code, row.city, row.shipment_id) == (
        "EG-C",
        "مدينة نصر",
        s.id,
    )
    assert (row.eta_min, row.eta_max) == (date(2026, 10, 20), date(2026, 10, 22))
    assert row.tenant_id == x.store.tenant_id  # from the order row
    # No name, phone or street is ever stored.
    assert set(ParcelRow.__table__.columns.keys()) == {
        "store_id",
        "order_id",
        "tenant_id",
        "shipment_id",
        "governorate_code",
        "city",
        "eta_min",
        "eta_max",
        "created_at",
        "updated_at",
    }


async def test_a_return_shipment_is_not_a_parcel(world):
    x = await world.build()
    o = await world.order(x)
    await handler.on_shipment_created(_created(await world.ship(x, o, kind="return")))
    assert await repo.parcel_row(world.db, x.store.id, o.id) is None


async def test_an_order_shipped_by_hand_gets_a_parcel(world):
    """TRK-I-02: no shipment; the status is read from the order."""
    x = await world.build()
    o = await world.order(x, status=OrderStatus.SHIPPED)
    await handler.on_order_status_changed(_order_event(o, "delivered"))
    assert await repo.parcel_row(world.db, x.store.id, o.id) is None
    await handler.on_order_status_changed(_order_event(o, "shipped"))
    row = await repo.parcel_row(world.db, x.store.id, o.id)
    assert row is not None and row.shipment_id is None
    [parcel] = await repo.parcels(world.db, x.store.id, _now() - svc.WINDOW)
    assert parcel.status == "in_transit"


async def test_a_new_shipment_repoints_the_parcel(world):
    x = await world.build()
    o = await world.order(x)
    first = await world.ship(x, o, status="cancelled")
    await handler.on_shipment_created(_created(first))
    second = await world.ship(x, o, status="created")
    second.created_at = _now() + timedelta(seconds=5)
    await world.db.flush()
    await handler.on_shipment_created(_created(second))
    assert (await repo.parcel_row(world.db, x.store.id, o.id)).shipment_id == second.id


async def test_a_tracking_number_change_alone_does_nothing(world):
    x = await world.build()
    o = await world.order(x)
    s = await world.ship(x, o, status="out_for_delivery", steps=("out_for_delivery",))
    await handler.on_shipment_status_changed(_changed(s, "out_for_delivery"))
    assert await repo.parcel_row(world.db, x.store.id, o.id) is None
    world.sends.assert_not_called()


# ─── TRK-I-03: reconcile ──────────────────────────────────────────────


async def test_reconcile(world):
    x = await world.build()
    now = _now()
    shipped = await world.order(x, created_at=now - timedelta(days=5))
    too_old = await world.order(x, created_at=now - timedelta(days=31))
    booked = await world.order(
        x, status=OrderStatus.PROCESSING, created_at=now - timedelta(days=2)
    )
    await world.ship(x, booked, status="created")
    await world.order(x, status=OrderStatus.PENDING)  # not shipped
    ancient = await world.order(x, created_at=now - timedelta(days=200))
    world.db.add(
        ParcelRow(
            store_id=x.store.id,
            order_id=ancient.id,
            tenant_id=x.store.tenant_id,
            created_at=now - timedelta(days=181),
            updated_at=now - timedelta(days=181),
        )
    )
    handled_long_ago, handled_lately = (
        Retry(
            store_id=x.store.id,
            tenant_id=x.store.tenant_id,
            order_id=shipped.id,
            day=date(2026, 9, 1),
            window="morning",
            note="جنب الصيدلية",
            handled_at=now - timedelta(days=days),
        )
        for days in (31, 10)
    )
    world.db.add_all([handled_long_ago, handled_lately])
    await world.db.flush()

    out = await tasks.reconcile(now=now)

    assert out["tracked"] == 2 and out["parcels"] == 1 and out["notes_blanked"] == 1
    tracked = set(
        (
            await world.db.execute(
                select(ParcelRow.order_id).where(ParcelRow.store_id == x.store.id)
            )
        )
        .scalars()
        .all()
    )
    assert tracked == {shipped.id, booked.id}
    assert too_old.id not in tracked and ancient.id not in tracked
    await world.db.refresh(handled_long_ago)
    await world.db.refresh(handled_lately)
    assert (handled_long_ago.note, handled_lately.note) == (None, "جنب الصيدلية")

    assert (await tasks.reconcile(now=now))["tracked"] == 0  # a second run: nothing


async def test_the_first_settings_call_queues_a_reconcile_for_that_store(world):
    """TRK-I-12, first half."""
    x = await world.build()
    assert await merchant.get_settings(x.session, world.db) == svc.DEFAULT_SETTINGS
    world.reconciles.assert_called_once_with(kwargs={"store_id": str(x.store.id)})
    await merchant.get_settings(x.session, world.db)
    await merchant.board(x.session, world.db)
    world.reconciles.assert_called_once()  # only the call that made the row

    o = await world.order(x)
    assert (await tasks.reconcile(x.store.id))["tracked"] == 1
    assert await repo.parcel_row(world.db, x.store.id, o.id) is not None


# ─── TRK-I-04 / S-12: the shopper's view ──────────────────────────────


@pytest.mark.parametrize(
    ("order_status", "shipment", "want"),
    [
        (OrderStatus.PENDING, None, "pending"),
        (OrderStatus.CONFIRMED, None, "confirmed"),
        (OrderStatus.PROCESSING, ("created", ()), "processing"),
        (OrderStatus.SHIPPED, ("picked_up", ("picked_up",)), "picked_up"),
        (
            OrderStatus.SHIPPED,
            ("in_transit", ("picked_up", "in_transit")),
            "in_transit",
        ),
        (
            OrderStatus.SHIPPED,
            ("out_for_delivery", ("picked_up", "in_transit", "out_for_delivery")),
            "out_for_delivery",
        ),
        (
            OrderStatus.DELIVERED,
            ("delivered", ("picked_up", "out_for_delivery", "delivered")),
            "delivered",
        ),
        (
            OrderStatus.SHIPPED,
            ("failed", ("picked_up", "out_for_delivery", "failed")),
            "failed",
        ),
        (
            OrderStatus.RETURNED,
            ("returned", ("picked_up", "out_for_delivery", "failed", "returned")),
            "returned",
        ),
        (OrderStatus.CANCELLED, None, "cancelled"),
    ],
)
async def test_the_shopper_view_per_state(world, order_status, shipment, want):
    x = await world.build()
    o = await world.order(x, status=order_status, zone_days=(1, 2))
    if shipment:
        status, steps = shipment
        delivered = {"delivered_at": _now()} if status == "delivered" else {}
        await world.ship(x, o, status=status, steps=steps, **delivered)

    out = await _view(world, x, o)

    assert out["status"] == want
    assert [s["key"] for s in out["steps"]][:1] in (["confirmed"], [want])
    assert len(out["steps"]) == (6 if want in ("returned", "cancelled") else 5)
    assert out["attempt"] == (1 if want == "failed" else None)
    assert out["retry"] == {"open": want == "failed", "sent": False}
    assert (out["delivered_at"] is not None) is (want == "delivered")
    assert out["eta"] is not None and out["delayed"] is False
    assert out["area"] == {
        "governorate_ar": "القاهرة",
        "governorate_en": "Cairo",
        "city": "مدينة نصر",
    }
    assert out["items"] == [{"name": "تيشيرت قطن", "variant": "M", "quantity": 1}]
    assert out["total"] == {"amount": 125000, "currency": "EGP"}
    assert out["contact"] == {"whatsapp": "0101 234 5678", "phone": "0223456789"}
    _assert_private(out)


async def test_a_parcel_past_its_window_is_late(world):
    x = await world.build()
    o = await world.order(x, zone_days=(1, 2), created_at=_now() - timedelta(days=9))
    await world.ship(x, o, status="in_transit", steps=("picked_up", "in_transit"))
    await handler.on_shipment_created(
        _created(await repo.latest_shipment(world.db, o.id))
    )
    out = await _view(world, x, o)
    assert out["delayed"] is True
    assert out["eta"]["max"] < _today().isoformat()


@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.DRAFT,
        OrderStatus.AWAITING_PAYMENT,
        OrderStatus.PAYMENT_FAILED,
        OrderStatus.PENDING_DEPOSIT,
    ],
)
async def test_orders_the_shopper_cannot_see_yet_are_404(world, status):
    x = await world.build()
    o = await world.order(x, status=status)
    with pytest.raises(HTTPException) as e:
        await _view(world, x, o)
    assert (e.value.status_code, e.value.detail) == (404, "not found")
    assert e.value.headers == {"Cache-Control": "private, no-store"}


async def test_an_unknown_id_and_another_stores_id_are_the_same_404(world):
    """TRK-S-02 / S-07."""
    x, y = await world.build(), await world.build()
    theirs = await world.order(y)
    errors = []
    for order_id in (uuid4(), theirs.id):
        with pytest.raises(HTTPException) as e:
            await shop.order_view(x.store.id, order_id, Response(), world.db)
        errors.append((e.value.status_code, e.value.detail, e.value.headers))
    assert errors[0] == errors[1] == (404, "not found", shop.NO_STORE)


# ─── TRK-S-03: the install gate on every entry point ──────────────────


@pytest.mark.parametrize(
    "case", ["no_install", "disabled", "pending_auth", "draft_app", "kill_switch"]
)
async def test_without_a_live_install_everything_stops(world, case):
    install = {
        "disabled": {"enabled": False},
        "pending_auth": {"status": "pending_auth"},
    }
    x = await world.build(live=case != "no_install", **install.get(case, {}))
    if case == "draft_app":
        x.app.status = AppStatus.DRAFT  # not published, and not its dev store
    if case == "kill_switch":
        await rows.kill_switch(world.db, on=False)
    await world.db.flush()
    o = await world.order(x)
    s = await world.ship(
        x, o, status="out_for_delivery", steps=("picked_up", "out_for_delivery")
    )
    world.db.add(
        Message(
            store_id=x.store.id,
            tenant_id=x.store.tenant_id,
            order_id=o.id,
            kind=svc.OUT_FOR_DELIVERY,
            attempt=1,
            status=svc.SCHEDULED,
            send_at=_now(),
        )
    )
    await world.db.flush()
    queued = (await world.db.execute(select(Message.id))).scalars().first()

    for call in (
        shop.order_view(x.store.id, o.id, Response(), world.db),
        shop.retry(
            x.store.id,
            o.id,
            shop.RetryIn(day=_today().isoformat(), window="morning"),
            world.db,
        ),
    ):
        with pytest.raises(HTTPException) as e:
            await call
        assert (e.value.status_code, e.value.detail) == (404, "not installed")

    await handler.on_shipment_created(_created(s))
    await handler.on_shipment_status_changed(_changed(s, "picked_up"))
    await handler.on_order_status_changed(_order_event(o, "shipped"))
    assert await repo.parcel_row(world.db, x.store.id, o.id) is None
    assert (await tasks.reconcile(x.store.id))["tracked"] == 0
    assert (await tasks.reconcile())["tracked"] == 0
    assert await tasks.sweep() == {"queued": 0}
    assert await tasks.send(queued) == {"status": "skipped", "reason": "not_installed"}
    world.sends.assert_not_called()


async def test_a_draft_app_is_live_on_its_developers_dev_store(world):
    """TRK-K-07: App 01's dev-store rule, through the shared gate."""
    from src.application.services.app_install_gate import is_live

    x = await world.build()
    x.app.status = AppStatus.DRAFT
    await world.db.flush()
    assert not await is_live(world.db, x.store.id, svc.SLUG)
    developer = await world.db.get(type(x.owner), x.app.developer_id)
    dev_store = await rows.store(world.db, developer, plan="developer")
    await rows.install(world.db, dev_store, x.app)
    assert await is_live(world.db, dev_store.id, svc.SLUG)


# ─── TRK-I-05 / S-14: "Try again" ─────────────────────────────────────


async def _failed(world, x, **kw):
    o = await world.order(x, **kw)
    s = await world.ship(
        x, o, status="failed", steps=("picked_up", "out_for_delivery", "failed")
    )
    return o, s


async def _try_again(world, x, o, *, day: date | None = None, note=None):
    body = shop.RetryIn(
        day=(day or _today() + timedelta(days=1)).isoformat(),
        window="afternoon",
        note=note,
    )
    return _body(await shop.retry(x.store.id, o.id, body, world.db))


async def test_try_again(world):
    x = await world.build()
    o, _s = await _failed(world, x)
    tomorrow = _today() + timedelta(days=1)

    code, body = await _try_again(world, x, o, note="<script>جنب الصيدلية</script>")
    assert (code, body) == (
        200,
        {"status": "sent", "day": tomorrow.isoformat(), "window": "afternoon"},
    )
    [request] = (await world.db.execute(select(Retry))).scalars().all()
    assert (request.day, request.window, request.note) == (
        tomorrow,
        "afternoon",
        "<script>جنب الصيدلية</script>",
    )
    [activity] = (await world.db.execute(select(OrderActivityModel))).scalars().all()
    assert (activity.kind, activity.user_id, activity.order_id) == (
        OrderActivityKind.SYSTEM_EVENT,
        None,
        o.id,
    )
    assert "عايز محاولة توصيل تانية" in activity.body and "afternoon" in activity.body
    assert (await _view(world, x, o))["retry"] == {"open": False, "sent": True}

    # A second one within 12 hours: refused, and no second comment.
    assert (await _try_again(world, x, o))[1]["code"] == "already_sent"
    assert await _count(world.db, OrderActivityModel) == 1

    # 13 hours later the shopper may ask again.
    request.created_at = _now() - timedelta(hours=13)
    await world.db.flush()
    assert (await _try_again(world, x, o))[0] == 200
    assert await _count(world.db, Retry) == 2


async def test_try_again_only_after_a_failure_and_within_three_days(world):
    x = await world.build()
    moving = await world.order(x)
    await world.ship(x, moving, status="in_transit", steps=("in_transit",))
    assert await _try_again(world, x, moving) == (
        409,
        {"code": "not_failed", "detail": "Delivery has not failed."},
    )
    o, _s = await _failed(world, x)
    for day in (_today() + timedelta(days=3), _today() - timedelta(days=1)):
        assert await _try_again(world, x, o, day=day) == (
            422,
            {"detail": "The day must be within the next 3 days."},
        )
    assert await _count(world.db, Retry) == 0  # nothing written
    assert (await _try_again(world, x, o, day=_today()))[0] == 200


# ─── TRK-I-06 / I-07: messages ────────────────────────────────────────


def _whatsapp(monkeypatch, *, access=True, template=None, opt_out=False):
    """Stub core's WhatsApp side: the store's state, the transport, the
    message log and the opt-in table. Returns the transport."""
    import src.infrastructure.events.handlers.whatsapp_notification_handler as notif
    import src.infrastructure.external_services.whatsapp as wa
    import src.infrastructure.repositories.whatsapp_opt_in_repository as optin

    template = template or {"ar": "APPROVED", "en": "APPROVED"}
    state = {
        "mode": "shared",
        "transport": "meta",
        "access": access,
        "access_reason": None if access else "not_requested",
        "credentials_ok": True,
        "needs_approved_template": True,
        "template": template,
        "template_category": dict.fromkeys(template, "UTILITY"),
    }
    monkeypatch.setattr(bisw, "whatsapp_state", AsyncMock(return_value=state))
    service = SimpleNamespace(
        send_message=AsyncMock(
            return_value=SimpleNamespace(
                success=True, message_id="wamid.1", error_code=None
            )
        )
    )
    monkeypatch.setattr(wa, "get_whatsapp_service", AsyncMock(return_value=service))
    monkeypatch.setattr(notif, "_persist_message_log", AsyncMock())
    monkeypatch.setattr(
        optin,
        "WhatsAppOptInRepository",
        lambda db: SimpleNamespace(
            get_active=AsyncMock(return_value=None),
            has_opt_out=AsyncMock(return_value=opt_out),
        ),
    )
    return service


async def _messages(db, store_id) -> list[Message]:
    return list(
        (
            await db.execute(
                select(Message)
                .where(Message.store_id == store_id)
                .order_by(Message.attempt)
            )
        )
        .scalars()
        .all()
    )


async def test_out_for_delivery_goes_out_once_with_its_four_parameters(
    world, monkeypatch
):
    """TRK-I-06."""
    service = _whatsapp(monkeypatch)
    x = await world.build()
    o = await world.order(x)
    s = await world.ship(
        x,
        o,
        status="out_for_delivery",
        steps=("picked_up", "in_transit", "out_for_delivery"),
    )

    await handler.on_shipment_status_changed(_changed(s, "in_transit"))
    await handler.on_shipment_status_changed(_changed(s, "in_transit"))  # a duplicate

    [message] = await _messages(world.db, x.store.id)
    assert (message.kind, message.attempt, message.status) == (
        svc.OUT_FOR_DELIVERY,
        1,
        svc.SCHEDULED,
    )
    world.sends.assert_called_once_with(args=[str(message.id)], eta=message.send_at)

    assert await tasks.send(message.id) == {"status": "sent", "reason": None}
    assert await tasks.send(message.id) == {"skipped": "not_scheduled"}
    sent = service.send_message.await_args.args[0]
    assert service.send_message.await_count == 1
    assert (str(sent.type), sent.recipient.phone, sent.recipient.language) == (
        "out_for_delivery",
        PHONE,
        "ar",
    )
    assert sent.template_params == {
        "customer_name": "منى",
        "order_number": o.order_number,
        "store_name": "Vionne",
        "order_id": f"{x.store.subdomain}/{o.id}",
    }
    await world.db.refresh(message)
    assert (message.status, message.provider_message_id) == (svc.SENT, "wamid.1")


@pytest.mark.parametrize(
    "closed", [OrderStatus.CANCELLED, OrderStatus.RETURNED, OrderStatus.REFUNDED]
)
async def test_a_waiting_message_is_dropped_once_the_order_closes(
    world, monkeypatch, closed
):
    """The merchant closes the order while the message waits; the shipment stays."""
    service = _whatsapp(monkeypatch)
    x = await world.build()
    o = await world.order(x)
    s = await world.ship(x, o, status="out_for_delivery", steps=("out_for_delivery",))
    await handler.on_shipment_status_changed(_changed(s, "in_transit"))
    [message] = await _messages(world.db, x.store.id)

    o.status = closed
    await world.db.flush()

    assert await tasks.send(message.id) == {"status": svc.SKIPPED, "reason": "stale"}
    service.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("switch_off", "switch_off"),
        ("cap", "cap"),
        ("no_access", "no_access"),
        ("not_approved", "template_not_approved"),
        ("opt_out", "opt_out"),
        ("no_phone", "no_phone"),
    ],
)
async def test_no_message_without_the_conditions(world, monkeypatch, setup, reason):
    """TRK-I-06: each refusal is recorded, nothing is sent."""
    service = _whatsapp(
        monkeypatch,
        access=setup != "no_access",
        template={"ar": "PENDING", "en": "REJECTED"}
        if setup == "not_approved"
        else None,
        opt_out=setup == "opt_out",
    )
    x = await world.build()
    if setup == "switch_off":
        await merchant.put_settings(
            x.session, world.db, merchant.SettingsIn(out_for_delivery_enabled=False)
        )
    if setup == "cap":
        await merchant.put_settings(
            x.session, world.db, merchant.SettingsIn(daily_cap=0)
        )
    o = await world.order(x, phone=None if setup == "no_phone" else "01098433918")
    if setup == "no_phone":
        x.customer.phone = None
        await world.db.flush()
    s = await world.ship(x, o, status="out_for_delivery", steps=("out_for_delivery",))

    await handler.on_shipment_status_changed(_changed(s, "in_transit"))
    [message] = await _messages(world.db, x.store.id)
    if message.status == svc.SCHEDULED:
        await tasks.send(message.id)
        await world.db.refresh(message)
    assert (message.status, message.skip_reason) == (svc.SKIPPED, reason)
    service.send_message.assert_not_awaited()


class _Clock(datetime):
    at: datetime

    @classmethod
    def now(cls, tz=None):
        return cls.at


async def test_a_failure_at_night_waits_and_a_second_failure_is_new(world, monkeypatch):
    """TRK-I-07."""
    _whatsapp(monkeypatch)
    x = await world.build()
    o = await world.order(x)
    night = datetime.combine(_today(), time(23, 30), tzinfo=CAIRO).astimezone(UTC)
    _Clock.at = night
    monkeypatch.setattr(handler, "datetime", _Clock)

    s = await world.ship(
        x,
        o,
        status="failed",
        steps=("picked_up", "out_for_delivery", "failed"),
        end=night,
    )
    await handler.on_shipment_status_changed(_changed(s, "out_for_delivery"))
    [first] = await _messages(world.db, x.store.id)
    nine = datetime.combine(_today() + timedelta(days=1), time(9), tzinfo=CAIRO)
    due = svc.aware(first.send_at)
    assert (first.kind, first.attempt) == (svc.DELIVERY_FAILED, 1)
    assert nine <= due < nine + timedelta(minutes=30)
    world.sends.assert_called_once_with(args=[str(first.id)], eta=first.send_at)

    # Out again, failed again: attempt 2 is a message of its own.
    s.status_history = s.status_history + [
        {"from": "failed", "to": "out_for_delivery", "timestamp": night.isoformat()},
        {"from": "out_for_delivery", "to": "failed", "timestamp": night.isoformat()},
    ]
    await world.db.flush()
    await handler.on_shipment_status_changed(_changed(s, "out_for_delivery"))
    assert [m.attempt for m in await _messages(world.db, x.store.id)] == [1, 2]

    # Delivered before 09:00: the morning message is stale and skipped.
    s.status = "delivered"
    await world.db.flush()
    assert await tasks.send(first.id) == {"status": "skipped", "reason": "stale"}


async def test_a_gowa_rate_limit_is_retried_not_dropped(world, monkeypatch):
    service = _whatsapp(monkeypatch)
    service.send_message.return_value = SimpleNamespace(
        success=False, message_id=None, error_code="rate_limited_minute"
    )
    x = await world.build()
    o = await world.order(x)
    s = await world.ship(x, o, status="out_for_delivery", steps=("out_for_delivery",))
    await handler.on_shipment_status_changed(_changed(s, "in_transit"))
    [message] = await _messages(world.db, x.store.id)

    with pytest.raises(tasks.SendLater) as later:
        await tasks.send(message.id, final_try=False)
    assert 90 <= later.value.countdown <= 120
    assert message.status == svc.SCHEDULED  # still due

    assert (await tasks.send(message.id))["status"] == svc.FAILED  # the last try
    assert message.skip_reason == "rate_limited_minute"


# ─── TRK-I-08: the message sweep ──────────────────────────────────────


async def test_the_sweep_sends_a_missed_change_once_and_never_late(world):
    x = await world.build()
    missed = await world.order(x)
    await world.ship(
        x,
        missed,
        status="out_for_delivery",
        steps=("in_transit", "out_for_delivery"),
        end=_now() - timedelta(minutes=30),
    )
    old = await world.order(x)
    await world.ship(
        x,
        old,
        status="failed",
        steps=("out_for_delivery", "failed"),
        end=_now() - timedelta(hours=3),
    )

    assert await tasks.sweep() == {"queued": 1}
    [message] = await _messages(world.db, x.store.id)
    assert (message.order_id, message.kind) == (missed.id, svc.OUT_FOR_DELIVERY)
    assert await tasks.sweep() == {"queued": 0}  # once

    # A scheduled message whose Celery message was lost is queued again.
    message.send_at = _now() - timedelta(minutes=20)
    await world.db.flush()
    assert await tasks.sweep() == {"queued": 1}

    # Two hours past due: skipped when it runs, never sent late.
    message.send_at = _now() - timedelta(hours=2, minutes=1)
    await world.db.flush()
    assert await tasks.send(message.id) == {"status": "skipped", "reason": "late"}


# ─── TRK-I-09 … I-12: the merchant routes ─────────────────────────────


async def rich_store(world) -> SimpleNamespace:
    """A store as the board sees it: every tile, every queue reason, and a
    Cairo/Bosta history big enough to rank."""
    x = await world.build()
    now, today = _now(), _today()

    async def parcel(
        status, steps, *, carrier="bosta", late=False, order_status=None, **kw
    ):
        o = await world.order(
            x,
            status=order_status or OrderStatus.SHIPPED,
            zone_days=(1, 2),
            created_at=now - timedelta(days=9 if late else 1),
        )
        s = await world.ship(x, o, status=status, steps=steps, carrier=carrier, **kw)
        await repo.track(world.db, o.id)
        return o, s

    asked, asked_s = await parcel("failed", ("picked_up", "out_for_delivery", "failed"))
    asked_s.extra_data = {"notes": "Fragile"}
    failed, _ = await parcel(
        "failed", ("picked_up", "out_for_delivery", "failed"), carrier="manual"
    )
    late, _ = await parcel("in_transit", ("picked_up", "in_transit"), late=True)
    await parcel(
        "delivered",
        ("picked_up", "out_for_delivery", "delivered"),
        order_status=OrderStatus.DELIVERED,
        delivered_at=now,
        shipped_at=now - timedelta(days=2),
    )
    await parcel("out_for_delivery", ("picked_up", "out_for_delivery"))
    await parcel(
        "returned",
        ("out_for_delivery", "failed", "returned"),
        order_status=OrderStatus.RETURNED,
    )
    await parcel("picked_up", ("picked_up",))
    for _ in range(8):  # history: enough Cairo/Bosta parcels to rank
        await parcel(
            "delivered",
            ("picked_up", "delivered"),
            order_status=OrderStatus.DELIVERED,
            delivered_at=now - timedelta(days=3),
            shipped_at=now - timedelta(days=6),
        )
    world.db.add(
        Retry(
            store_id=x.store.id,
            tenant_id=x.store.tenant_id,
            order_id=asked.id,
            day=today + timedelta(days=1),
            window="afternoon",
            note="جنب الصيدلية",
        )
    )
    await world.db.flush()
    return SimpleNamespace(x=x, asked=asked, failed=failed, late=late)


async def test_the_board_counts_and_the_queue(world):
    """TRK-I-09."""
    r = await rich_store(world)
    out = await merchant.board(r.x.session, world.db)
    assert out["counts"] == {
        "in_transit": 2,  # picked_up + in_transit
        "out_for_delivery": 1,
        "delivered_today": 1,
        "delayed": 1,
        "failed": 2,
        "returned": 1,
    }
    assert [(q["reason"], q["order_id"]) for q in out["queue"]] == [
        ("retry_request", str(r.asked.id)),
        ("failed", str(r.failed.id)),
        ("delayed", str(r.late.id)),
    ]
    first = out["queue"][0]
    assert first["retry"]["note"] == "جنب الصيدلية" and first["can_courier_note"]
    assert out["queue"][1]["can_courier_note"] is False  # manual courier
    assert out["queue"][2]["days_late"] >= 1
    _assert_private(out)


async def test_parcels_filters_search_and_paging(world):
    r = await rich_store(world)
    s, db = r.x.session, world.db
    assert (await merchant.parcels(s, db))["total"] == 15
    in_transit = await merchant.parcels(s, db, status="in_transit")
    assert in_transit["total"] == 2
    assert {i["status"] for i in in_transit["items"]} == {"picked_up", "in_transit"}
    found = await merchant.parcels(s, db, q=f"#{r.asked.order_number}")
    assert [i["order_id"] for i in found["items"]] == [str(r.asked.id)]
    page = await merchant.parcels(s, db, skip=10, limit=10)
    assert len(page["items"]) == 5 and page["total"] == 15
    assert (await merchant.parcels(s, db, status="delayed"))["items"][0]["delayed"]


async def test_the_drawer(world):
    r = await rich_store(world)
    out = await merchant.parcel(r.x.session, world.db, r.asked.id)
    assert out["status"] == "failed" and out["attempts"] == 1
    assert [st["key"] for st in out["steps"]] == [
        "confirmed",
        "picked_up",
        "out_for_delivery",
        "failed",
    ]
    assert out["retry"]["handled_at"] is None and out["can_courier_note"] is True
    assert out["tracking_url"].startswith("https://bosta.co/")
    _assert_private(out)


async def test_merchant_routes_stay_in_the_tokens_store(world):
    """TRK-S-02."""
    r = await rich_store(world)
    y = await world.build()
    assert (await merchant.board(y.session, world.db))["counts"]["failed"] == 0
    assert (await merchant.parcels(y.session, world.db))["total"] == 0
    for call in (
        merchant.parcel(y.session, world.db, r.asked.id),
        merchant.phone(y.session, world.db, r.asked.id),
        merchant.handled(y.session, world.db, r.asked.id),
        merchant.courier_note(y.session, world.db, r.asked.id),
    ):
        with pytest.raises(HTTPException) as e:
            await call
        assert e.value.status_code == 404
    assert await _count(world.db, Retry, Retry.handled_at.is_not(None)) == 0


async def test_analytics_and_messages(world, monkeypatch):
    r = await rich_store(world)
    analytics = await merchant.analytics(r.x.session, world.db)
    assert analytics["success_rate"] == round(9 / 10, 2)  # 9 delivered, 1 returned
    assert [a["area"]["governorate_code"] for a in analytics["areas"]] == ["EG-C"]
    assert [c["carrier"]["key"] for c in analytics["carriers"]] == ["bosta"]

    world.db.add_all(
        Message(
            store_id=r.x.store.id,
            tenant_id=r.x.store.tenant_id,
            order_id=r.asked.id,
            kind=svc.DELIVERY_FAILED,
            attempt=i,
            status=status,
            skip_reason=reason,
        )
        for i, (status, reason) in enumerate(
            [(svc.SENT, None), (svc.SKIPPED, "cap"), (svc.SKIPPED, "no_access")], 1
        )
    )
    await world.db.flush()
    out = await merchant.messages(r.x.session, world.db)
    assert (out["sent_30d"], out["skipped_30d"]) == (1, 2)
    assert out["skip_reasons"] == {"cap": 1, "no_access": 1}
    assert out["language"] == "ar" and out["daily_cap"] == 300
    assert out["preview"][svc.OUT_FOR_DELIVERY].startswith(
        "أهلاً منى، طلبك رقم 1042 من Vionne"
    )
    assert out[svc.DELIVERY_FAILED]["template"] == {"ar": None, "en": None}


async def test_the_phone_is_read_live_and_never_logged(world, caplog, capsys):
    """TRK-I-10."""
    r = await rich_store(world)
    caplog.clear()  # the fixture's own inserts are logged by the SQLite driver
    capsys.readouterr()
    assert await merchant.phone(r.x.session, world.db, r.asked.id) == {"phone": PHONE}
    logged = caplog.text + capsys.readouterr().out
    for model in TABLES:  # no app table holds it
        for row in (await world.db.execute(select(model))).scalars():
            dumped = json.dumps(
                {c: str(getattr(row, c)) for c in model.__table__.columns.keys()},
                ensure_ascii=False,
            )
            assert "1098433918" not in dumped
    assert "1098433918" not in logged


async def test_the_courier_note(world, monkeypatch):
    """TRK-I-11."""
    r = await rich_store(world)
    s, db = r.x.session, world.db
    bosta = SimpleNamespace(update_delivery=AsyncMock(return_value={}))
    monkeypatch.setattr(merchant, "service_for_shipment", AsyncMock(return_value=bosta))

    assert await merchant.courier_note(s, db, r.asked.id) == {"status": "sent"}
    shipment = await repo.latest_shipment(db, r.asked.id)
    note = f"Fragile\nالعميل عايز يستلم يوم {_today() + timedelta(days=1)} بعد الضهر (12–5): جنب الصيدلية"
    bosta.update_delivery.assert_awaited_once_with(shipment.tracking_number, notes=note)
    assert shipment.extra_data == {"notes": note}  # nothing else changed

    assert (
        _body(await merchant.courier_note(s, db, r.asked.id))[1]["code"]
        == "already_sent"
    )
    assert bosta.update_delivery.await_count == 1
    assert _body(await merchant.courier_note(s, db, r.failed.id)) == (
        409,
        {"code": "carrier_unsupported", "detail": "This carrier takes no notes."},
    )
    assert (await merchant.handled(s, db, r.asked.id)) == {"status": "handled"}
    assert (
        _body(await merchant.handled(s, db, r.asked.id))[1]["code"] == "no_open_request"
    )


async def test_a_courier_refusal_marks_nothing_sent(world, monkeypatch):
    r = await rich_store(world)
    bosta = SimpleNamespace(update_delivery=AsyncMock(side_effect=RuntimeError("502")))
    monkeypatch.setattr(merchant, "service_for_shipment", AsyncMock(return_value=bosta))
    assert (
        _body(await merchant.courier_note(r.x.session, world.db, r.asked.id))[0] == 502
    )
    retry = await repo.newest_retry(world.db, r.x.store.id, r.asked.id)
    assert retry.courier_notified_at is None


async def test_settings_change_what_the_shopper_sees(world):
    """TRK-I-12, second half."""
    x = await world.build()
    o = await world.order(x)
    saved = await merchant.put_settings(
        x.session, world.db, merchant.SettingsIn(show_items=False, show_contact=False)
    )
    assert saved == {**svc.DEFAULT_SETTINGS, "show_items": False, "show_contact": False}
    out = await _view(world, x, o)
    assert (out["show_items"], out["show_contact"]) == (False, False)
    assert not {"items", "total", "contact"} & set(out)
    for bad in ({"daily_cap": 5001}, {"daily_cap": -1}, {"colour": "red"}):
        with pytest.raises(ValidationError):
            merchant.SettingsIn(**bad)
    assert merchant.SettingsIn(daily_cap=0).daily_cap == 0


# ─── TRK-S-06 / K-07: uninstall, purge, data rights ───────────────────


async def test_uninstall_stops_a_queued_send_and_the_purge_leaves_nothing(
    world, monkeypatch
):
    from src.application.services.numu_apps import PURGERS, purge_due

    service = _whatsapp(monkeypatch)
    r = await rich_store(world)
    await merchant.get_settings(r.x.session, world.db)
    s = await repo.latest_shipment(world.db, r.asked.id)
    s.status = "out_for_delivery"
    s.status_history = s.status_history + [
        {"from": "failed", "to": "out_for_delivery", "timestamp": _now().isoformat()}
    ]
    await world.db.flush()
    await handler.on_shipment_status_changed(_changed(s, "failed"))
    [queued] = [
        m for m in await _messages(world.db, r.x.store.id) if m.status == svc.SCHEDULED
    ]

    install = await world.db.scalar(
        select(AppInstallationModel).where(
            AppInstallationModel.store_id == r.x.store.id
        )
    )
    await world.db.delete(install)
    await world.db.flush()
    assert await tasks.send(queued.id) == {
        "status": "skipped",
        "reason": "not_installed",
    }
    service.send_message.assert_not_awaited()

    assert "order-tracking" in PURGERS
    world.db.add(
        AppUninstallModel(
            store_id=r.x.store.id,
            app_id=r.x.app.id,
            purge_after=_now() - timedelta(days=1),
        )
    )
    await world.db.flush()
    assert (await purge_due(world.db))["purged"] == 1
    for model in TABLES:
        assert await _count(world.db, model, model.store_id == r.x.store.id) == 0


async def test_data_rights_export_and_delete(world):
    from src.api.v1.routes.storefront import data_rights

    r = await rich_store(world)
    world.db.add(
        Message(
            store_id=r.x.store.id,
            tenant_id=r.x.store.tenant_id,
            order_id=r.asked.id,
            kind=svc.DELIVERY_FAILED,
            attempt=1,
            status=svc.SENT,
        )
    )
    await world.db.flush()
    shopper = SimpleNamespace(
        id=r.x.customer.id,
        store_id=r.x.store.id,
        phone=PHONE,
        email=EMAIL,
        first_name="منى",
        last_name="س",
        accepts_marketing=False,
        notification_preferences={},
    )
    exported = await data_rights._order_tracking_export(
        SimpleNamespace(session=world.db), shopper
    )
    assert len(exported["parcels"]) == 15
    assert [rq["note"] for rq in exported["retry_requests"]] == ["جنب الصيدلية"]
    assert [m["kind"] for m in exported["messages"]] == [svc.DELIVERY_FAILED]

    await data_rights.delete_account(
        Response(),
        shopper,
        SimpleNamespace(session=world.db, update=AsyncMock()),
        SimpleNamespace(get_by_customer=AsyncMock(return_value=[]), delete=AsyncMock()),
    )
    for model in (ParcelRow, Retry, Message):
        assert await _count(world.db, model, model.store_id == r.x.store.id) == 0


# ─── Wiring ───────────────────────────────────────────────────────────


def test_the_retry_has_its_own_rate_limit_tier():
    from src.api.middleware.rate_limit import _is_order_tracking_retry

    base = f"/api/v1/storefront/store/{uuid4()}/apps/order-tracking/orders/{uuid4()}"
    assert _is_order_tracking_retry(base + "/retry")
    assert not _is_order_tracking_retry(base)
    assert not _is_order_tracking_retry(
        f"/api/v1/storefront/store/{uuid4()}/apps/back-in-stock/orders/x/retry"
    )


def test_one_shopper_is_limited_while_another_still_passes(monkeypatch):
    """TRK-S-07: the host relay sends each shopper's IP; the retry tier
    buckets per shopper, 10 a minute."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from src.api.middleware import rate_limit

    token = "t" * 48
    monkeypatch.setattr(rate_limit.settings, "internal_service_token", token)
    monkeypatch.setattr(rate_limit.settings, "rate_limit_enabled", True)
    counts: dict[tuple[str, str], int] = {}

    async def fake_check(bucket, tier, limit):
        counts[bucket, tier] = counts.get((bucket, tier), 0) + 1
        return counts[bucket, tier] <= limit, counts[bucket, tier], 60

    monkeypatch.setattr(rate_limit, "_check_rate_limit", fake_check)

    async def ok(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/{path:path}", ok, methods=["POST"])])
    app.add_middleware(rate_limit.RateLimitMiddleware)
    client = TestClient(app)
    path = (
        f"/api/v1/storefront/store/{uuid4()}/apps/order-tracking/orders/{uuid4()}/retry"
    )

    def tap(ip):
        return client.post(
            path, headers={"x-internal-service-token": token, "x-forwarded-for": ip}
        ).status_code

    assert [tap("41.1.1.1") for _ in range(11)] == [200] * 10 + [429]
    assert tap("41.2.2.2") == 200
    assert ("41.1.1.1", "order_tracking_retry") in counts


def test_routes_are_mounted_and_core_tracking_still_is():
    from fastapi.routing import APIRoute

    from src.main import app

    paths = {r.path for r in app.routes if isinstance(r, APIRoute)}
    assert "/api/v1/storefront/track/{order_id}" in paths  # core's page
    assert (
        "/api/v1/storefront/store/{store_id}/apps/order-tracking/orders/{order_id}"
        in paths
    )
    assert "/api/v1/apps/order-tracking/board" in paths
    guarded = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1/apps/order-tracking/")
    ]
    assert len(guarded) == 11  # every one behind its session (test_apps_router.py)


def test_the_handlers_are_subscribed(monkeypatch):
    from src.infrastructure.events import setup

    # A bus of our own: no commit listeners left behind for other tests.
    monkeypatch.setattr(setup, "_event_bus", None)
    monkeypatch.setattr(setup, "install_commit_watch", lambda bus: None)
    bus = setup.create_event_bus()
    for event, fn in (
        ("ShipmentCreatedEvent", handler.on_shipment_created),
        ("ShipmentStatusChangedEvent", handler.on_shipment_status_changed),
        ("OrderStatusChangedEvent", handler.on_order_status_changed),
    ):
        assert fn in bus._handlers[event]
