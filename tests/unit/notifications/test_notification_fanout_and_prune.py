"""Fan-out (realtime publish + important web-push) and retention prune."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import src.application.services.notification_feed as feed_mod
from src.api.v1.routes.stores.notifications import SSE_PING, sse_frame
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.merchant_notification import (
    MerchantNotificationModel,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.messaging.tasks.notification_center_tasks import (
    prune_merchant_notifications,
)


@pytest.fixture
def factory(test_engine, monkeypatch):
    f = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(feed_mod, "AsyncSessionLocal", f)
    return f


@pytest.fixture
def captured(monkeypatch):
    """Swap the two side-effects for recorders."""
    calls: dict[str, list] = {"realtime": [], "push": []}

    async def fake_publish(result):
        calls["realtime"].append(result)

    def fake_push(result):
        calls["push"].append(result)

    monkeypatch.setattr(feed_mod, "_publish_realtime", fake_publish)
    monkeypatch.setattr(feed_mod, "_enqueue_push", fake_push)
    return calls


async def _seed_store(session, *, settings=None, language="ar") -> StoreModel:
    tenant = TenantModel(
        id=uuid4(),
        name="Tenant",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="free",
        lifecycle_state="active",
    )
    session.add(tenant)
    await session.flush()
    store = StoreModel(
        id=uuid4(),
        tenant_id=tenant.id,
        owner_id=uuid4(),
        name="Store",
        slug=f"s-{uuid4().hex[:6]}",
        subdomain=f"s-{uuid4().hex[:6]}",
        status="active",
        default_currency="EGP",
        default_language=language,
        settings=settings or {},
        theme_settings={},
        social_links={},
    )
    session.add(store)
    await session.commit()
    return store


@pytest.mark.asyncio
async def test_standalone_emit_fans_out_after_commit(test_session, factory, captured):
    store = await _seed_store(test_session)

    normal = await feed_mod.emit_notification_standalone(
        store_id=store.id,
        category="orders",
        kind="order.new",
        data={"order_number": "ORD-1"},
        dedupe_key="order.new:1",
    )
    important = await feed_mod.emit_notification_standalone(
        store_id=store.id,
        category="orders",
        kind="order.cancelled",
        data={"order_number": "ORD-1", "total_cents": 72900, "currency": "EGP"},
        important=True,
        dedupe_key="order.cancelled:1",
    )
    replay = await feed_mod.emit_notification_standalone(
        store_id=store.id,
        category="orders",
        kind="order.cancelled",
        important=True,
        dedupe_key="order.cancelled:1",
    )

    assert normal and important and not replay
    assert important.owner_id == store.owner_id
    assert [r.kind for r in captured["realtime"]] == ["order.new", "order.cancelled"]
    assert [r.kind for r in captured["push"]] == ["order.cancelled"]


@pytest.mark.asyncio
async def test_push_respects_store_opt_out(test_session, factory, captured):
    store = await _seed_store(
        test_session, settings={"push_notifications": {"important": False}}
    )
    await feed_mod.emit_notification_standalone(
        store_id=store.id,
        category="payments",
        kind="payment.failed",
        important=True,
    )
    assert len(captured["realtime"]) == 1
    assert captured["push"] == []


def test_push_copy_is_pii_free_and_bilingual():
    base = {
        "written": True,
        "store_id": uuid4(),
        "tenant_id": uuid4(),
        "important": True,
        # Rich details OFF → lock-screen body is amount only.
        "store_settings": {"push_notifications": {"rich_details": False}},
        "data": {
            "order_number": "ORD-9",
            "customer_name": "Yahia Sherif",
            "total_cents": 150000,
            "currency": "EGP",
        },
    }
    ar = feed_mod.push_copy(
        feed_mod.EmitResult(kind="order.cancelled", language="ar", **base)
    )
    en = feed_mod.push_copy(
        feed_mod.EmitResult(kind="order.cancelled", language="en", **base)
    )
    assert ar == ("تم إلغاء الطلب #ORD-9", "1,500 ج.م")
    assert en == ("Order #ORD-9 cancelled", "EGP 1,500")
    for title, body in (ar, en):
        assert "Yahia" not in title + body

    ks = feed_mod.push_copy(
        feed_mod.EmitResult(
            kind="trust.kill_switch",
            language="en",
            **{**base, "data": {"rate_pct": 42.5}},
        )
    )
    assert ks == ("Trust auto-approve paused", "RTO rate 42.5%")
    assert (
        feed_mod.push_copy(
            feed_mod.EmitResult(
                kind="order.new", language="en", **{**base, "important": False}
            )
        )
        is None
    )


def test_push_copy_rich_details_reads_like_the_email():
    res = feed_mod.EmitResult(
        written=True,
        store_id=uuid4(),
        tenant_id=uuid4(),
        important=True,
        kind="order.cancelled",
        language="en",
        store_settings={},  # default → rich on
        data={
            "order_number": "ORD-9",
            "customer_name": "Yahia Sherif",
            "payment_method": "cod",
            "total_cents": 13421,
            "currency": "EGP",
            "reason": "no stock",
        },
    )
    title, body = feed_mod.push_copy(res)
    assert title == "Order #ORD-9 cancelled"
    assert body == "Yahia Sherif · Cash on delivery · EGP 134 · no stock"

    body = feed_mod.rich_push_body(
        customer_name="يحيى",
        items_count=2,
        payment_method="vodafone_cash",
        amount="134 ج.م",
        created_at=None,
        store_settings=None,
        is_ar=True,
    )
    assert body == "يحيى · 2 منتج · فودافون كاش · 134 ج.م"


def test_sse_framing():
    assert sse_frame({"type": "tick"}) == 'data: {"type": "tick"}' + "\n" * 2
    assert SSE_PING.startswith(":") and SSE_PING.endswith("\n" * 2)


@pytest.mark.asyncio
async def test_prune_deletes_by_age_and_read_state(test_session, factory):
    store = await _seed_store(test_session)
    now = datetime.now(UTC)

    def row(*, days: int, read: bool):
        created = now - timedelta(days=days)
        return MerchantNotificationModel(
            id=uuid4(),
            tenant_id=store.tenant_id,
            store_id=store.id,
            category="orders",
            kind="order.new",
            data={},
            read_at=created if read else None,
            created_at=created,
        )

    test_session.add_all([
        row(days=1, read=True),  # keep
        row(days=100, read=True),  # read > 90d → pruned
        row(days=100, read=False),  # unread, < 180d → keep
        row(days=200, read=False),  # > 180d → pruned
    ])
    await test_session.commit()

    stats = await prune_merchant_notifications(now=now, session_factory=factory)
    assert stats == {"read_pruned": 1, "old_pruned": 1}

    remaining = (
        await test_session.execute(
            select(func.count(MerchantNotificationModel.id)).where(
                MerchantNotificationModel.store_id == store.id
            )
        )
    ).scalar()
    assert remaining == 2
