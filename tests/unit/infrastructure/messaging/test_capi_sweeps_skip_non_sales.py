"""The Purchase sweeps never report an order that is not a sale.

Found 2026-10-14 while preparing a test order on a live store: the hourly
Meta and TikTok orphan sweeps picked every recent COD order and every paid
order that had no Purchase row, whatever its status. A COD order the merchant
cancelled as fake, a refunded or returned order and a draft were all sent to
the ad platform as a Purchase, inflating the store's conversions.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.application.services import tiktok_capi_purchase_dispatcher
from src.core.entities.order import OrderStatus
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.messaging.tasks import meta_capi, tiktok_capi

NOT_A_SALE = (
    OrderStatus.CANCELLED,
    OrderStatus.REFUNDED,
    OrderStatus.RETURNED,
    OrderStatus.DRAFT,
)
BOTH = {
    "meta": {"pixel_id": "1", "api_enabled": True, "access_token": "t"},
    "tiktok": {"pixel_id": "2", "api_enabled": True, "access_token": "t"},
}


def _order(store_id, status, *, hours_ago=1, **fields):
    at = datetime.now(UTC) - timedelta(hours=hours_ago)
    return OrderModel(
        id=uuid4(),
        tenant_id=store_id,
        store_id=store_id,
        customer_id=uuid4(),
        order_number=f"ORD-{uuid4().hex[:6]}",
        status=status,
        shipping_address={},
        created_at=at,
        updated_at=at,
        **fields,
    )


def _store(tracking):
    now = datetime.now(UTC)
    return StoreModel(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_id=uuid4(),
        name="Store",
        slug=f"s-{uuid4().hex[:8]}",
        subdomain=f"s-{uuid4().hex[:8]}",
        status="active",
        default_currency="EGP",
        default_language="ar",
        settings={"tracking": tracking},
        theme_settings={},
        social_links={},
        business_hours={},
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def sweep_db(test_session, monkeypatch):
    """The tasks open their own session: hand them the test one, RLS off."""

    @asynccontextmanager
    async def _session():
        yield test_session

    monkeypatch.setattr(
        "src.infrastructure.database.connection.AsyncSessionLocal", _session
    )
    monkeypatch.setattr("src.infrastructure.tenancy.rls.enable_rls_bypass", AsyncMock())
    return test_session


async def test_meta_sweep_filter_skips_orders_that_are_not_a_sale(test_session):
    store_id = uuid4()
    now = datetime.now(UTC)
    test_session.add_all([
        *(_order(store_id, s, payment_method="cod") for s in NOT_A_SALE),
        _order(store_id, OrderStatus.PENDING, payment_method="cod"),
        *(
            _order(store_id, s, payment_method="paymob_card", paid_at=now)
            for s in NOT_A_SALE
        ),
        _order(
            store_id, OrderStatus.PROCESSING, payment_method="paymob_card", paid_at=now
        ),
    ])
    await test_session.flush()

    rows = await test_session.execute(
        select(OrderModel.payment_method, OrderModel.status).where(
            meta_capi._sweep_order_filter(now - timedelta(hours=24))
        )
    )

    assert sorted((m, s.value) for m, s in rows) == [
        ("cod", "pending"),
        ("paymob_card", "processing"),
    ]


async def test_tiktok_sweep_skips_orders_that_are_not_a_sale(sweep_db, monkeypatch):
    now = datetime.now(UTC)
    # "true" as a string: SQLite's JSON_EXTRACT reads a JSON true as 1, never
    # the text 'true' Postgres' ->> returns, so the sweep's store filter only
    # matches the string form here.
    store = _store({"tiktok": {"pixel_id": "2", "api_enabled": "true"}})
    sweep_db.add_all([
        store,
        *(
            _order(store.id, s, payment_method="paymob_card", paid_at=now)
            for s in (*NOT_A_SALE, OrderStatus.PROCESSING)
        ),
    ])
    await sweep_db.flush()
    sent = []

    async def _enqueue(_session, order):
        sent.append(order.status.value)

    monkeypatch.setattr(
        tiktok_capi_purchase_dispatcher, "enqueue_tiktok_capi_purchase", _enqueue
    )

    await tiktok_capi._sweep_orphans(lookback_hours=24)

    assert sent == ["processing"]


async def test_purchase_gap_alert_ignores_orders_that_are_not_a_sale(sweep_db):
    """With the sweeps fixed, a store whose only orders are not sales sends no
    Purchase, and that is correct, not a gap."""
    quiet, silent = _store(BOTH), _store(BOTH)
    sweep_db.add_all([
        quiet,
        silent,
        *(_order(quiet.id, s, hours_ago=30) for s in NOT_A_SALE),
        _order(silent.id, OrderStatus.PENDING, hours_ago=30),
    ])
    await sweep_db.flush()

    gaps = await tiktok_capi._find_purchase_gaps()

    assert sorted((g["store_id"], g["platform"]) for g in gaps) == [
        (str(silent.id), "meta"),
        (str(silent.id), "tiktok"),
    ]
