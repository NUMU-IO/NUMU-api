"""Unit tests — commission charge / reversal handlers on the event bus.

The handlers open their own ``AsyncSessionLocal``; we patch that symbol
to the test engine's factory so they run against the same in-memory DB
the fixtures write to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import src.infrastructure.events.handlers.wallet_commission_handler as handler_mod
from src.application.services.wallet_settings import (
    invalidate_wallet_settings_cache,
)
from src.core.events.order_events import OrderPaidEvent, OrderStatusChangedEvent
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.wallet import (
    MerchantWalletModel,
    WalletTransactionModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel


@pytest.fixture(autouse=True)
def _fresh_wallet_settings():
    invalidate_wallet_settings_cache()
    yield
    invalidate_wallet_settings_cache()


@pytest.fixture
def patched_sessions(test_engine, monkeypatch):
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(handler_mod, "AsyncSessionLocal", factory)
    return factory


async def _seed(session, *, plan="payg", total=10_000, currency="EGP"):
    tenant = TenantModel(
        id=uuid4(),
        name="Payg Tenant",
        subdomain=f"payg-{uuid4().hex[:8]}",
        plan=plan,
        lifecycle_state="active",
    )
    store = StoreModel(
        id=uuid4(),
        tenant_id=tenant.id,
        owner_id=uuid4(),
        name="Store",
        slug=f"s-{uuid4().hex[:6]}",
        subdomain=f"s-{uuid4().hex[:6]}",
        status="active",
        default_currency="EGP",
        default_language="ar",
        settings={},
        theme_settings={},
        social_links={},
        business_hours={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    order = OrderModel(
        id=uuid4(),
        tenant_id=tenant.id,
        store_id=store.id,
        customer_id=uuid4(),
        order_number=f"ORD-{uuid4().hex[:6]}",
        status="processing",
        payment_status="PAID",
        fulfillment_status="UNFULFILLED",
        subtotal=total,
        total=total,
        currency=currency,
        shipping_address={},
        paid_at=datetime.now(UTC),
    )
    session.add_all([tenant, store, order])
    await session.commit()
    return tenant, store, order


def _paid_event(order, store) -> OrderPaidEvent:
    return OrderPaidEvent(
        order_id=order.id,
        order_number=order.order_number,
        store_id=store.id,
        customer_id=order.customer_id,
        payment_method="paymob",
        total=float(order.total),
    )


@pytest.mark.asyncio
async def test_commission_charged_once_for_paid_order(test_session, patched_sessions):
    tenant, store, order = await _seed(test_session, total=10_000)

    await handler_mod.handle_commission_charge_on_order_paid(_paid_event(order, store))
    # Duplicate delivery (webhook retry) — must be a no-op.
    await handler_mod.handle_commission_charge_on_order_paid(_paid_event(order, store))

    txs = (
        (
            await test_session.execute(
                select(WalletTransactionModel).where(
                    WalletTransactionModel.tenant_id == tenant.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(txs) == 1
    # 10,000 cents × 300 bps = 300 cents, floored, negative.
    assert txs[0].amount_cents == -300
    assert txs[0].kind == "commission"

    wallet = (
        await test_session.execute(
            select(MerchantWalletModel).where(
                MerchantWalletModel.tenant_id == tenant.id
            )
        )
    ).scalar_one()
    assert wallet.balance_cents == -300


@pytest.mark.asyncio
async def test_subscription_tenant_is_never_charged(test_session, patched_sessions):
    tenant, store, order = await _seed(test_session, plan="starter")

    await handler_mod.handle_commission_charge_on_order_paid(_paid_event(order, store))

    count = (
        (
            await test_session.execute(
                select(WalletTransactionModel).where(
                    WalletTransactionModel.tenant_id == tenant.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert count == []


@pytest.mark.asyncio
async def test_full_refund_reverses_commission_once(test_session, patched_sessions):
    tenant, store, order = await _seed(test_session, total=20_000)

    await handler_mod.handle_commission_charge_on_order_paid(_paid_event(order, store))

    refund_event = OrderStatusChangedEvent(
        order_id=order.id,
        order_number=order.order_number,
        store_id=store.id,
        store_name=store.name,
        customer_id=order.customer_id,
        previous_status="processing",
        new_status="refunded",
    )
    await handler_mod.handle_commission_reversal_on_refund(refund_event)
    # Double delivery — second reversal must be a no-op.
    await handler_mod.handle_commission_reversal_on_refund(refund_event)

    wallet = (
        await test_session.execute(
            select(MerchantWalletModel).where(
                MerchantWalletModel.tenant_id == tenant.id
            )
        )
    ).scalar_one()
    assert wallet.balance_cents == 0

    kinds = sorted(
        t.kind
        for t in (
            await test_session.execute(
                select(WalletTransactionModel).where(
                    WalletTransactionModel.tenant_id == tenant.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert kinds == ["commission", "commission_reversal"]


@pytest.mark.asyncio
async def test_non_refund_status_changes_are_ignored(test_session, patched_sessions):
    tenant, store, order = await _seed(test_session)
    await handler_mod.handle_commission_charge_on_order_paid(_paid_event(order, store))

    event = OrderStatusChangedEvent(
        order_id=order.id,
        order_number=order.order_number,
        store_id=store.id,
        store_name=store.name,
        customer_id=order.customer_id,
        previous_status="processing",
        new_status="shipped",
    )
    await handler_mod.handle_commission_reversal_on_refund(event)

    txs = (
        (
            await test_session.execute(
                select(WalletTransactionModel).where(
                    WalletTransactionModel.tenant_id == tenant.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [t.kind for t in txs] == ["commission"]


@pytest.mark.asyncio
async def test_missing_fx_rate_skips_charge(test_session, patched_sessions):
    tenant, store, order = await _seed(test_session, currency="SAR")

    await handler_mod.handle_commission_charge_on_order_paid(_paid_event(order, store))

    txs = (
        (
            await test_session.execute(
                select(WalletTransactionModel).where(
                    WalletTransactionModel.tenant_id == tenant.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert txs == []  # skipped loudly, reconciliation will retry
