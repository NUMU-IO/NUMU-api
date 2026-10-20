"""Reviews app «آراء العملاء» (docs/Plans/APPS/03-reviews, § Version 1.0).

A delivered order gets one review request inside the store's send window; its
link opens the store's write page; the buyer's reviews are core
``product_reviews`` rows the merchant moderates in the app.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException, Response
from PIL import Image
from sqlalchemy import func, select
from starlette.datastructures import FormData, Headers, UploadFile

from src.api.dependencies.app_session import AppSession
from src.api.v1.routes.apps import reviews as merchant
from src.api.v1.routes.storefront import apps_reviews as shop
from src.application.services import back_in_stock_whatsapp as bisw
from src.application.services import reviews_service as svc
from src.core.entities.order import OrderStatus
from src.core.entities.product import ProductStatus
from src.core.events.order_events import OrderStatusChangedEvent
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.product_review import (
    ProductReviewModel as Review,
)
from src.infrastructure.database.models.tenant.reviews import (
    ReviewsRequestModel as Request,
)
from src.infrastructure.database.models.tenant.reviews import (
    ReviewsSettingsModel as Settings,
)
from src.infrastructure.events import commit_watch
from src.infrastructure.events.handlers import reviews_handler as handler
from src.infrastructure.messaging.tasks import reviews_tasks as tasks
from src.infrastructure.repositories import reviews_repository as repo
from tests.unit import app_platform_rows as rows
from tests.unit.test_app_install_gate import _Session
from tests.unit.test_back_in_stock_app import _send_lock

CAIRO = ZoneInfo("Africa/Cairo")
PHONE = "+201098433918"


def _at_cairo(hour: int, minute: int = 0, days: int = 0) -> datetime:
    day = datetime.now(CAIRO).date() + timedelta(days=days)
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=CAIRO
    ).astimezone(UTC)


@pytest.fixture
def world(monkeypatch, test_session):
    """Stores with the app live, and the outside world stubbed."""
    monkeypatch.setattr(commit_watch, "_bus", None)
    for module in (tasks, handler):
        monkeypatch.setattr(module, "AsyncSessionLocal", lambda: _Session(test_session))
        monkeypatch.setattr(module, "enable_rls_bypass", AsyncMock())
    monkeypatch.setattr(tasks, "_sending", _send_lock(True))
    sends = MagicMock()
    monkeypatch.setattr(tasks.send_task, "apply_async", sends)
    cache = SimpleNamespace(invalidate_store_products=AsyncMock())

    async def commit(db):  # SQLite has no set_config; the test session flushes
        await db.flush()

    for module in (merchant, shop):
        monkeypatch.setattr(module, "get_product_cache", lambda: cache)
        monkeypatch.setattr(module, "commit_and_restore_rls", commit)
    the_app: list = []

    async def build(*, live: bool = True) -> SimpleNamespace:
        owner = await rows.user(test_session)
        store = await rows.store(test_session, owner)
        store.name = "Vionne"
        store.settings = {"timezone": "Africa/Cairo"}
        if not the_app:
            partner = await rows.user(test_session)
            the_app.append(
                await rows.app(test_session, slug=svc.SLUG, developer=partner)
            )
        made = the_app[0]
        if live:
            await rows.install(test_session, store, made.app)
        customer = CustomerModel(
            id=uuid4(),
            tenant_id=store.tenant_id,
            store_id=store.id,
            email="mona@example.com",
            phone=PHONE,
            first_name="منى",
            last_name="س",
        )
        products = [
            ProductModel(
                id=uuid4(),
                tenant_id=store.tenant_id,
                store_id=store.id,
                name=name,
                slug=f"p-{uuid4().hex[:6]}",
                status=ProductStatus.ACTIVE,
                quantity=5,
                images=[f"https://cdn.example.com/{name}.jpg"],
            )
            for name in ("فستان مودال", "طرحة شيفون")
        ]
        test_session.add_all([customer, *products])
        await test_session.flush()
        return SimpleNamespace(
            store=store,
            customer=customer,
            products=products,
            session=AppSession(
                store_id=store.id, user_id=owner.id, app_id=made.app.id, locale="ar"
            ),
        )

    async def order(x, *, status=OrderStatus.DELIVERED) -> OrderModel:
        o = OrderModel(
            id=uuid4(),
            tenant_id=x.store.tenant_id,
            store_id=x.store.id,
            customer_id=x.customer.id,
            order_number=f"10{uuid4().int % 100000:05d}",
            status=status,
            total=125000,
            currency="EGP",
            shipping_address={
                "first_name": "منى",
                "last_name": "س",
                "address_line1": "١٢ شارع التحرير",
                "city": "مدينة نصر",
                "country": "EG",
                "phone": "01098433918",
            },
            line_items=[
                {
                    "product_id": str(p.id),
                    "product_name": p.name,
                    "quantity": 1,
                    "unit_price": 62500,
                    "total_price": 62500,
                }
                for p in x.products
            ],
        )
        test_session.add(o)
        await test_session.flush()
        return o

    return SimpleNamespace(
        build=build, order=order, db=test_session, sends=sends, cache=cache
    )


def _event(o: OrderModel, new: str) -> OrderStatusChangedEvent:
    return OrderStatusChangedEvent(
        order_id=o.id,
        order_number=o.order_number,
        store_id=o.store_id,
        store_name="Vionne",
        customer_id=o.customer_id,
        previous_status="shipped",
        new_status=new,
    )


async def _requests(db, store_id) -> list[Request]:
    return list(
        (await db.execute(select(Request).where(Request.store_id == store_id)))
        .scalars()
        .all()
    )


def _whatsapp(monkeypatch, *, access=True, opt_out=False):
    """Stub core's WhatsApp side; returns the transport."""
    import src.infrastructure.events.handlers.whatsapp_notification_handler as notif
    import src.infrastructure.external_services.whatsapp as wa
    import src.infrastructure.repositories.whatsapp_opt_in_repository as optin

    template = {"ar": "APPROVED", "en": "APPROVED"}
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


# ─── Rules ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("delivered", "due"),
    [
        ((9, 0), (13, 10)),  # 3 days later is 09:00: wait for 10:00
        ((22, 0), (14, 10)),  # 3 days later is after 21:00: next morning
        ((12, 30), (13, 12)),  # inside the window: as is
        ((20, 59), (13, 20)),
    ],
)
def test_a_request_is_due_after_the_delay_inside_the_send_window(delivered, due):
    """A January week, so no daylight-saving change moves the hour."""
    at = datetime(2026, 1, 10, *delivered, tzinfo=CAIRO)
    local = svc.due_at(at, 3, "Africa/Cairo", rand=lambda: 0).astimezone(CAIRO)
    assert (local.day, local.hour) == due


def test_tabs_tokens_and_moderation_settings():
    assert svc.tab_of(True, None) == "published"
    assert svc.tab_of(False, None) == "pending"
    assert svc.tab_of(False, "spam") == "spam"
    assert svc.tab_of(False, "abuse") == "rejected"
    token = svc.new_token()
    assert len(token) >= 21 and svc.token_hash(token) != token
    assert len(svc.token_hash(token)) == 64
    now = datetime.now(UTC)
    assert svc.link_open(now - timedelta(days=59), now)
    assert not svc.link_open(now - timedelta(days=61), now)
    assert not svc.link_open(None, now)
    assert svc.moderation({"review_moderation": "manual"}) == {
        "moderation_mode": "manual",
        "flagged_words": [],
    }
    assert svc.moderation({})["moderation_mode"] == "auto"


# ─── The handler ───────────────────────────────────────────────────────


async def test_delivered_makes_one_request_and_closing_the_order_calls_it_off(world):
    x = await world.build()
    o = await world.order(x)
    await handler.on_order_status_changed(_event(o, "delivered"))
    await handler.on_order_status_changed(_event(o, "delivered"))  # a duplicate
    [r] = await _requests(world.db, x.store.id)
    assert r.status == svc.SCHEDULED
    assert svc.aware(r.due_at) > datetime.now(UTC) + timedelta(days=2)

    await handler.on_order_status_changed(_event(o, "returned"))
    await world.db.refresh(r)
    assert (r.status, r.skip_reason) == (svc.CANCELLED, "order_closed")


async def test_the_handler_does_nothing_without_the_app_or_with_requests_off(world):
    x = await world.build(live=False)
    await handler.on_order_status_changed(_event(await world.order(x), "delivered"))
    assert await _requests(world.db, x.store.id) == []

    y = await world.build()
    world.db.add(
        Settings(
            store_id=y.store.id,
            tenant_id=y.store.tenant_id,
            **(svc.DEFAULT_SETTINGS | {"requests_enabled": False}),
        )
    )
    await world.db.flush()
    await handler.on_order_status_changed(_event(await world.order(y), "delivered"))
    [r] = await _requests(world.db, y.store.id)
    assert (r.status, r.skip_reason) == (svc.SKIPPED, "switch_off")


# ─── Sweep and send ────────────────────────────────────────────────────


async def _due_request(world, x, o) -> Request:
    await handler.on_order_status_changed(_event(o, "delivered"))
    [r] = [r for r in await _requests(world.db, x.store.id) if r.order_id == o.id]
    r.due_at = r.updated_at = datetime.now(UTC) - timedelta(days=1)
    await world.db.flush()
    return r


async def test_the_sweep_queues_due_requests_only_inside_the_window_and_the_cap(world):
    x = await world.build()
    r = await _due_request(world, x, await world.order(x))

    assert await tasks.sweep(_at_cairo(23)) == {"queued": 0}
    assert await tasks.sweep(_at_cairo(12)) == {"queued": 1}
    world.sends.assert_called_once_with(args=[str(r.id)])
    # Queued and not sent yet: the next sweeps leave it to its task...
    assert await tasks.sweep(_at_cairo(12, 10)) == {"queued": 0}
    # ...until the lease runs out (a lost task).
    assert await tasks.sweep(_at_cairo(13, 30)) == {"queued": 1}

    world.db.add(
        Settings(
            store_id=x.store.id,
            tenant_id=x.store.tenant_id,
            **(svc.DEFAULT_SETTINGS | {"daily_cap": 0}),
        )
    )
    await world.db.flush()
    assert await tasks.sweep(_at_cairo(12)) == {"queued": 0}


async def test_queued_requests_count_against_the_daily_cap(world):
    """A request queued but not sent yet takes its place in the cap."""
    x = await world.build()
    world.db.add(
        Settings(
            store_id=x.store.id,
            tenant_id=x.store.tenant_id,
            **(svc.DEFAULT_SETTINGS | {"daily_cap": 1}),
        )
    )
    await _due_request(world, x, await world.order(x))
    assert await tasks.sweep(_at_cairo(12)) == {"queued": 1}
    # Another order comes due while the first is still in flight.
    await _due_request(world, x, await world.order(x))
    assert await tasks.sweep(_at_cairo(12, 10)) == {"queued": 0}


async def test_a_due_request_goes_out_once_with_a_working_link(world, monkeypatch):
    service = _whatsapp(monkeypatch)
    x = await world.build()
    o = await world.order(x)
    r = await _due_request(world, x, o)

    assert await tasks.send(r.id) == {"status": svc.SENT, "reason": None}
    assert await tasks.send(r.id) == {"skipped": "not_scheduled"}
    service.send_message.assert_awaited_once()
    content = service.send_message.await_args.args[0]
    params = content.template_params
    assert params["customer_name"] == "منى"
    assert params["order_number"] == o.order_number
    assert params["store_name"] == "Vionne"
    prefix = f"reviews/{x.store.subdomain}/"
    assert params["review_link"].startswith(prefix)
    token = params["review_link"][len(prefix) :]
    await world.db.refresh(r)
    assert r.token_hash == svc.token_hash(token) and r.sent_at is not None

    url = await shop.resolve_link(world.db, f"{x.store.subdomain}/{token}")
    assert url == f"https://{x.store.subdomain}.numueg.app/reviews/write?t={token}"
    await world.db.refresh(r)
    assert r.opened_at is not None
    assert await shop.resolve_link(world.db, f"no-such-store/{token}") is None


async def test_a_request_whatsapp_took_is_never_sent_twice(world, monkeypatch):
    """If the database fails after the provider accepted the message, the
    request is already marked sent: no sweep sends it again."""
    import src.infrastructure.events.handlers.whatsapp_notification_handler as notif

    service = _whatsapp(monkeypatch)
    monkeypatch.setattr(
        notif, "_persist_message_log", AsyncMock(side_effect=RuntimeError("db gone"))
    )
    x = await world.build()
    r = await _due_request(world, x, await world.order(x))
    with pytest.raises(RuntimeError):
        await tasks.send(r.id)
    service.send_message.assert_awaited_once()
    await world.db.refresh(r)
    assert r.status == svc.SENT
    assert (
        await repo.due(world.db, x.store.id, datetime.now(UTC) + timedelta(days=1), 10)
        == []
    )


async def test_a_failure_before_whatsapp_is_asked_leaves_the_request_for_later(
    world, monkeypatch
):
    _whatsapp(monkeypatch)
    monkeypatch.setattr(
        bisw, "whatsapp_state", AsyncMock(side_effect=RuntimeError("down"))
    )
    x = await world.build()
    r = await _due_request(world, x, await world.order(x))
    with pytest.raises(RuntimeError):
        await tasks.send(r.id)
    await world.db.refresh(r)
    assert (r.status, r.sent_at, r.token_hash) == (svc.SCHEDULED, None, None)


async def test_two_first_calls_share_one_settings_row(world, monkeypatch):
    """The front's first screens can ask at once; the second insert is a no-op."""
    x = await world.build()
    world.db.add(
        Settings(
            store_id=x.store.id, tenant_id=x.store.tenant_id, **svc.DEFAULT_SETTINGS
        )
    )
    await world.db.flush()
    real = repo.settings_row
    calls: list[int] = []

    async def first_misses(db, store_id):
        calls.append(1)
        return None if len(calls) == 1 else await real(db, store_id)

    monkeypatch.setattr(repo, "settings_row", first_misses)
    row = await repo.ensure_settings(world.db, x.store)
    assert row is not None and row.store_id == x.store.id


@pytest.mark.parametrize(
    ("setup", "reason"),
    [("opt_out", "opt_out"), ("returned", "order_closed"), ("reviewed", "reviewed")],
)
async def test_a_request_that_must_not_go_is_skipped_with_its_reason(
    world, monkeypatch, setup, reason
):
    service = _whatsapp(monkeypatch, opt_out=setup == "opt_out")
    x = await world.build()
    o = await world.order(x)
    r = await _due_request(world, x, o)
    if setup == "returned":
        o.status = OrderStatus.RETURNED
    if setup == "reviewed":
        for p in x.products:
            world.db.add(_review(x, product=p, order=o))
    await world.db.flush()

    assert await tasks.send(r.id) == {"status": svc.SKIPPED, "reason": reason}
    if setup != "opt_out":
        service.send_message.assert_not_awaited()


# ─── The write page ────────────────────────────────────────────────────


async def _sent(world, x, o) -> str:
    r = await _due_request(world, x, o)
    token = svc.new_token()
    r.status, r.token_hash, r.sent_at = (
        svc.SENT,
        svc.token_hash(token),
        datetime.now(UTC),
    )
    await world.db.flush()
    return token


def _photo(*, exif: bool = True) -> bytes:
    img = Image.new("RGB", (2400, 1600), (200, 120, 90))
    out = io.BytesIO()
    if exif:
        data = Image.Exif()
        data[0x010F] = "PhoneMaker"  # Make
        img.save(out, format="JPEG", exif=data.tobytes())
    else:
        img.save(out, format="JPEG")
    return out.getvalue()


def _form(token: str, fields: dict, photos: dict | None = None) -> SimpleNamespace:
    items = [("t", token), *fields.items()]
    for key, blobs in (photos or {}).items():
        for i, blob in enumerate(blobs):
            items.append((
                key,
                UploadFile(
                    io.BytesIO(blob),
                    filename=f"p{i}.jpg",
                    headers=Headers({"content-type": "image/jpeg"}),
                ),
            ))
    return SimpleNamespace(form=AsyncMock(return_value=FormData(items)))


@pytest.fixture
def storage(monkeypatch):
    import src.api.dependencies.services as services

    stored: dict[str, bytes] = {}

    async def upload_file(*, file_content, key, **_kw):
        stored[key] = file_content
        return SimpleNamespace(key=key, url=f"https://media.example.com/{key}")

    fake = SimpleNamespace(
        upload_file=upload_file,
        delete_file=AsyncMock(),
        list_files=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(services, "get_storage_service", lambda: fake)
    return SimpleNamespace(stored=stored, fake=fake)


async def test_the_write_page_answers_for_its_own_store_only(world):
    x = await world.build()
    o = await world.order(x)
    token = await _sent(world, x, o)

    page = await shop.write_page(x.store.id, world.db, Response(), t=token)
    assert page["state"] == "open" and page["order_number"] == o.order_number
    assert [i["name"] for i in page["items"]] == ["فستان مودال", "طرحة شيفون"]
    assert not any(i["reviewed"] for i in page["items"])

    other = await world.build()
    assert (await shop.write_page(other.store.id, world.db, Response(), t=token))[
        "state"
    ] == "not_found"
    assert (await shop.write_page(x.store.id, world.db, Response(), t="nope"))[
        "state"
    ] == "not_found"

    [r] = await _requests(world.db, x.store.id)
    r.sent_at = datetime.now(UTC) - timedelta(days=61)
    await world.db.flush()
    assert (await shop.write_page(x.store.id, world.db, Response(), t=token))[
        "state"
    ] == "expired"

    off = await world.build(live=False)
    with pytest.raises(HTTPException) as exc:
        await shop.write_page(off.store.id, world.db, Response(), t=token)
    assert exc.value.status_code == 404 and exc.value.detail == "not installed"


async def test_a_buyer_reviews_with_photos_and_the_store_rule_decides(world, storage):
    x = await world.build()
    x.store.settings = {"timezone": "Africa/Cairo", "review_moderation": "manual"}
    o = await world.order(x)
    token = await _sent(world, x, o)
    dress, scarf = (str(p.id) for p in x.products)

    out = await shop.write(
        x.store.id,
        world.db,
        _form(
            token,
            {f"rating.{dress}": "4", f"body.{dress}": "الخامة حلوة جداً", "name": ""},
            {f"photos.{dress}": [_photo(), b"not an image"]},
        ),
        Response(),
    )
    assert out == {"created": 1, "published": 0, "photos_rejected": 1}
    [review] = (await world.db.execute(select(Review))).scalars().all()
    assert (review.verified, review.order_id, review.is_approved) == (True, o.id, False)
    assert review.reviewer_name == "منى" and review.rating == 4
    [photo] = review.media
    full = Image.open(io.BytesIO(storage.stored[photo["key"]]))
    assert full.format == "WEBP" and max(full.size) == svc.PHOTO_SIZE
    assert not full.getexif()
    assert max(Image.open(io.BytesIO(storage.stored[photo["thumb_key"]])).size) == 400
    assert photo["key"].startswith(f"reviews/{x.store.id}/")

    page = await shop.write_page(x.store.id, world.db, Response(), t=token)
    assert [i["reviewed"] for i in page["items"]] == [True, False]
    [r] = await _requests(world.db, x.store.id)
    assert r.status == svc.COMPLETED and r.completed_at is not None

    # The dress again is ignored; the scarf alone is rated now.
    out = await shop.write(
        x.store.id,
        world.db,
        _form(token, {f"rating.{dress}": "1", f"rating.{scarf}": "5"}),
        Response(),
    )
    assert out["created"] == 1
    # "No photos" is SQL NULL, not JSON null, so the photo filter counts right.
    assert (
        await world.db.scalar(
            select(func.count()).select_from(Review).where(Review.media.is_(None))
        )
        == 1
    )
    _rows, with_photos = await repo.inbox(
        world.db, x.store.id, tab="pending", photos=True
    )
    _rows, without = await repo.inbox(world.db, x.store.id, tab="pending", photos=False)
    assert (with_photos, without) == (1, 1)


async def test_a_change_shoppers_see_is_committed_before_the_cache_is_busted(
    world, monkeypatch
):
    x = await world.build()
    held = _review(x)
    world.db.add(held)
    await world.db.flush()
    order: list[str] = []
    monkeypatch.setattr(
        merchant,
        "commit_and_restore_rls",
        AsyncMock(side_effect=lambda db: order.append("commit")),
    )
    world.cache.invalidate_store_products.side_effect = lambda store_id: order.append(
        "bust"
    )
    await merchant.act(
        x.session, world.db, held.id, merchant.ActionIn(action="publish")
    )
    assert order == ["commit", "bust"]


async def test_photos_are_handled_with_no_transaction_open(world, storage, monkeypatch):
    """Postgres ends a transaction idle for 60 s; photos take a while."""
    x = await world.build()
    o = await world.order(x)
    token = await _sent(world, x, o)
    seen: list[bool] = []
    real = shop._store_photo

    async def spy(store_id, data):
        seen.append(world.db.in_transaction())
        return await real(store_id, data)

    monkeypatch.setattr(shop, "_store_photo", spy)
    pid = str(x.products[0].id)
    out = await shop.write(
        x.store.id,
        world.db,
        _form(token, {f"rating.{pid}": "5"}, {f"photos.{pid}": [_photo(exif=False)]}),
        Response(),
    )
    assert out["created"] == 1 and seen == [False]


async def test_a_duplicate_post_leaves_no_photos_behind(world, storage, monkeypatch):
    """The same form sent twice: the second insert conflicts, and its photos go."""
    from sqlalchemy.exc import IntegrityError

    x = await world.build()
    o = await world.order(x)
    token = await _sent(world, x, o)
    calls: list[int] = []

    async def commit(db):
        calls.append(1)
        if len(calls) == 2:  # the insert's commit: the other post won
            raise IntegrityError("INSERT", {}, Exception("duplicate"))
        await db.flush()

    monkeypatch.setattr(shop, "commit_and_restore_rls", commit)
    pid = str(x.products[0].id)
    with pytest.raises(HTTPException) as exc:
        await shop.write(
            x.store.id,
            world.db,
            _form(
                token, {f"rating.{pid}": "5"}, {f"photos.{pid}": [_photo(exif=False)]}
            ),
            Response(),
        )
    assert exc.value.status_code == 409
    deleted = {c.args[0] for c in storage.fake.delete_file.await_args_list}
    assert deleted == set(storage.stored) and len(deleted) == 2


async def test_a_failed_upload_removes_the_photos_already_stored(
    world, storage, monkeypatch
):
    x = await world.build()
    o = await world.order(x)
    token = await _sent(world, x, o)
    uploads: list[str] = []
    real = storage.fake.upload_file

    async def flaky(**kw):
        uploads.append(kw["key"])
        if len(uploads) == 3:  # the second photo's full size
            raise RuntimeError("R2 timed out")
        return await real(**kw)

    storage.fake.upload_file = flaky
    pid = str(x.products[0].id)
    with pytest.raises(RuntimeError):
        await shop.write(
            x.store.id,
            world.db,
            _form(
                token,
                {f"rating.{pid}": "5"},
                {f"photos.{pid}": [_photo(exif=False), _photo(exif=False)]},
            ),
            Response(),
        )
    deleted = {c.args[0] for c in storage.fake.delete_file.await_args_list}
    assert deleted == set(storage.stored) and len(deleted) == 2


async def test_a_fully_refunded_order_takes_no_review(world, monkeypatch):
    """Some refund flows leave the order delivered and set only the payment."""
    from src.core.entities.order import PaymentStatus

    service = _whatsapp(monkeypatch)
    x = await world.build()
    o = await world.order(x)
    token = await _sent(world, x, o)
    o.payment_status = PaymentStatus.REFUNDED
    await world.db.flush()
    page = await shop.write_page(x.store.id, world.db, Response(), t=token)
    assert page["state"] == "not_found"

    y = await world.build()
    p = await world.order(y)
    r = await _due_request(world, y, p)
    p.payment_status = PaymentStatus.REFUNDED
    await world.db.flush()
    assert await tasks.send(r.id) == {"status": svc.SKIPPED, "reason": "order_closed"}
    service.send_message.assert_not_awaited()

    p.payment_status = PaymentStatus.PARTIALLY_REFUNDED
    assert svc.reviewable(p)


@pytest.mark.parametrize(
    "fields",
    [{"rating.X": "6"}, {"rating.X": "4", "body.X": "x" * 4001}, {}],
)
async def test_a_bad_form_is_refused_and_nothing_is_written(world, storage, fields):
    x = await world.build()
    o = await world.order(x)
    token = await _sent(world, x, o)
    pid = str(x.products[0].id)
    fields = {k.replace("X", pid): v for k, v in fields.items()}
    with pytest.raises(HTTPException) as exc:
        await shop.write(x.store.id, world.db, _form(token, fields), Response())
    assert exc.value.status_code == 422
    assert await world.db.scalar(select(func.count()).select_from(Review)) == 0


# ─── Merchant routes ───────────────────────────────────────────────────


def _review(x, *, product=None, order=None, approved=False, reason=None, rating=4):
    return Review(
        id=uuid4(),
        tenant_id=x.store.tenant_id,
        store_id=x.store.id,
        product_id=(product or x.products[0]).id,
        customer_id=x.customer.id,
        order_id=order.id if order else None,
        verified=order is not None,
        reviewer_name="منى",
        rating=rating,
        body="حلو",
        is_approved=approved,
        rejected_reason=reason,
    )


async def test_the_inbox_moderates_core_reviews_including_held_ones(world):
    x = await world.build()
    held = _review(x)  # a core review "manual" mode held forever (Bug 12)
    world.db.add(held)
    await world.db.flush()

    page = await merchant.reviews(x.session, world.db, tab="pending")
    assert [i["id"] for i in page["items"]] == [str(held.id)]
    assert page["counts"] == {"pending": 1, "published": 0, "rejected": 0, "spam": 0}

    await merchant.act(
        x.session, world.db, held.id, merchant.ActionIn(action="publish")
    )
    assert held.is_approved and held.rejected_reason is None
    world.cache.invalidate_store_products.assert_awaited_with(x.store.id)

    with pytest.raises(HTTPException):
        await merchant.act(
            x.session, world.db, held.id, merchant.ActionIn(action="reject")
        )
    await merchant.act(
        x.session, world.db, held.id, merchant.ActionIn(action="reject", reason="abuse")
    )
    assert (await merchant.reviews(x.session, world.db, tab="rejected"))["total"] == 1

    more = [_review(x) for _ in range(3)]
    world.db.add_all(more)
    await world.db.flush()
    world.cache.invalidate_store_products.reset_mock()
    out = await merchant.bulk(
        x.session, world.db, merchant.BulkIn(ids=[r.id for r in more], action="spam")
    )
    assert out == {"updated": 3}
    world.cache.invalidate_store_products.assert_awaited_once()

    replied = await merchant.reply(
        x.session, world.db, held.id, merchant.ReplyIn(body=" شكراً لرأيك ")
    )
    assert replied["reply"]["body"] == "شكراً لرأيك"

    await merchant.remove(x.session, world.db, held.id)
    assert await world.db.get(Review, held.id) is None


async def test_another_store_cannot_touch_a_review_by_id(world):
    a, b = await world.build(), await world.build()
    theirs = _review(b)
    world.db.add(theirs)
    await world.db.flush()
    for call in (
        merchant.act(
            a.session, world.db, theirs.id, merchant.ActionIn(action="publish")
        ),
        merchant.reply(a.session, world.db, theirs.id, merchant.ReplyIn(body="x")),
        merchant.remove(a.session, world.db, theirs.id),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404
    out = await merchant.bulk(
        a.session, world.db, merchant.BulkIn(ids=[theirs.id], action="publish")
    )
    assert out == {"updated": 0} and theirs.is_approved is False


async def test_settings_write_the_stores_own_moderation_rule(world):
    x = await world.build()
    first = await merchant.get_settings(x.session, world.db)
    assert first == svc.DEFAULT_SETTINGS | {
        "moderation_mode": "auto",
        "flagged_words": [],
    }
    out = await merchant.put_settings(
        x.session,
        world.db,
        merchant.SettingsIn(
            delay_days=5,
            moderation_mode="auto_with_filter",
            flagged_words=["نصب", " نصب "],
        ),
    )
    assert out["delay_days"] == 5
    assert x.store.settings["review_moderation"] == {
        "mode": "auto_with_filter",
        "flagged_words": ["نصب"],
    }
    assert x.store.settings["timezone"] == "Africa/Cairo"


# ─── The product page ──────────────────────────────────────────────────


async def test_the_product_page_gets_the_summary_only_with_the_app(world):
    x = await world.build()
    o = await world.order(x)
    dress = x.products[0]
    assert await repo.payload_section(world.db, x.store.id, dress.id) is None
    world.db.add_all([
        _review(x, order=o, approved=True, rating=5),
        _review(x, approved=True, rating=4),
        _review(x, approved=False, rating=1),
    ])
    await world.db.flush()
    assert await repo.payload_section(world.db, x.store.id, dress.id) == {
        "average": 4.5,
        "count": 2,
    }
    page = await shop.product_reviews_first(x.store.id, dress.id, world.db, Response())
    assert page["summary"]["count"] == 2 and len(page["reviews"]) == 2
    assert {r["verified"] for r in page["reviews"]} == {True, False}

    off = await world.build(live=False)
    assert await repo.payload_section(world.db, off.store.id, dress.id) is None


# ─── Uninstall and data rights ─────────────────────────────────────────


async def test_the_purge_drops_the_apps_data_and_keeps_the_text(world, storage):
    x = await world.build()
    o = await world.order(x)
    await _due_request(world, x, o)
    await repo.ensure_settings(world.db, x.store)
    kept = _review(x, order=o, approved=True)
    kept.reply_body, kept.media = "شكراً", [{"url": "u", "key": "k"}]
    world.db.add(kept)
    await world.db.flush()

    await repo.purge_store(world.db, x.store.id)
    assert await _requests(world.db, x.store.id) == []
    assert (
        await world.db.scalar(
            select(func.count()).select_from(Review).where(Review.media.is_(None))
        )
        == 1
    )
    assert await world.db.get(Settings, x.store.id) is None
    await world.db.refresh(kept)
    assert (kept.reply_body, kept.media, kept.body) == (None, None, "حلو")
    storage.fake.list_files.assert_awaited_with(f"reviews/{x.store.id}/")


async def test_data_rights_cover_the_review_requests(world):
    from src.api.v1.routes.storefront import data_rights

    x = await world.build()
    o = await world.order(x)
    await _due_request(world, x, o)
    exported = await data_rights._reviews_export(
        SimpleNamespace(session=world.db),
        SimpleNamespace(id=x.customer.id, store_id=x.store.id),
    )
    assert [e["order_id"] for e in exported] == [str(o.id)]
    assert await repo.delete_for(world.db, x.store.id, [o.id]) == 1
    assert await _requests(world.db, x.store.id) == []


# ─── Wiring ────────────────────────────────────────────────────────────


def test_the_app_is_wired_in():
    from src.api.v1.routes.app_links import APP_LINKS
    from src.application.services.numu_apps import PURGERS
    from src.infrastructure.messaging.celery_app import celery_app

    assert APP_LINKS[svc.SLUG] is shop.resolve_link
    assert svc.SLUG in PURGERS
    beat = {e["task"] for e in celery_app.conf.beat_schedule.values()}
    assert "apps.reviews.sweep" in beat
