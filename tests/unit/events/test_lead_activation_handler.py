"""Unit tests — the merchant-lead activation milestone on order paid.

``first_order_at`` and the ``activated`` status were read by the admin
funnel and written by nothing, so the last column of the funnel was
structurally always zero. These tests pin the write down, and pin down
the two ways it could regress into corrupting the funnel instead: a
webhook retry moving the activation date forward, and a second store's
first order re-activating a merchant who activated months ago.

Like the commission handler, this one opens its own ``AsyncSessionLocal``,
so that symbol is patched to the test engine's factory.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import src.infrastructure.events.handlers.lead_activation_handler as handler_mod
from src.core.events.order_events import OrderPaidEvent
from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel


@pytest.fixture
def patched_sessions(test_engine, monkeypatch):
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(handler_mod, "AsyncSessionLocal", factory)
    return factory


async def _seed(session, *, with_lead=True, lead_status="store_created", **lead_kw):
    tenant = TenantModel(
        id=uuid4(),
        name="Tenant",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="payg",
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
        subtotal=10_000,
        total=10_000,
        currency="EGP",
        shipping_address={},
        paid_at=datetime.now(UTC),
    )
    rows = [tenant, store, order]
    lead = None
    if with_lead:
        lead = MerchantLeadModel(
            email=f"m-{uuid4().hex[:8]}@example.com",
            source="signup",
            status=lead_status,
            tenant_id=tenant.id,
            **lead_kw,
        )
        rows.append(lead)
    session.add_all(rows)
    await session.commit()
    return tenant, store, order, lead


def _paid_event(order, store) -> OrderPaidEvent:
    return OrderPaidEvent(
        order_id=order.id,
        order_number=order.order_number,
        store_id=store.id,
        customer_id=order.customer_id,
        payment_method="cod",
        total=float(order.total),
    )


async def _reload(session, lead_id):
    return (
        await session.execute(
            select(MerchantLeadModel).where(MerchantLeadModel.id == lead_id)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_first_paid_order_activates_the_lead(test_session, patched_sessions):
    _, store, order, lead = await _seed(test_session)
    assert lead.first_order_at is None
    # Read the id before expiring — afterwards every attribute access is
    # a lazy load, and this session is not the one the handler used.
    lead_id = lead.id

    await handler_mod.handle_lead_activation_on_order_paid(_paid_event(order, store))

    test_session.expire_all()
    refreshed = await _reload(test_session, lead_id)
    assert refreshed.status == "activated"
    assert refreshed.first_order_at is not None


@pytest.mark.asyncio
async def test_redelivery_does_not_move_the_activation_date(
    test_session, patched_sessions
):
    """A webhook retry must not rewrite when this merchant activated.

    ``first_order_at`` is the input to time-to-activation, so a handler
    that overwrites it on every delivery would quietly compress every
    merchant's activation time toward zero.
    """
    original = datetime.now(UTC) - timedelta(days=30)
    _, store, order, lead = await _seed(
        test_session, lead_status="activated", first_order_at=original
    )
    lead_id = lead.id

    await handler_mod.handle_lead_activation_on_order_paid(_paid_event(order, store))

    test_session.expire_all()
    refreshed = await _reload(test_session, lead_id)
    assert refreshed.first_order_at is not None
    # SQLite hands back naive datetimes where Postgres returns aware ones;
    # compare on the wall clock so this asserts the behaviour, not the driver.
    stored = refreshed.first_order_at.replace(tzinfo=None)
    assert abs((stored - original.replace(tzinfo=None)).total_seconds()) < 1


@pytest.mark.asyncio
async def test_status_is_not_dragged_backwards(test_session, patched_sessions):
    """Already ``activated`` stays ``activated``."""
    _, store, order, lead = await _seed(test_session, lead_status="activated")
    lead_id = lead.id

    await handler_mod.handle_lead_activation_on_order_paid(_paid_event(order, store))

    test_session.expire_all()
    assert (await _reload(test_session, lead_id)).status == "activated"


@pytest.mark.asyncio
async def test_tenant_without_a_lead_is_silent(test_session, patched_sessions):
    """Tenants predating the leads table must not raise on their next order."""
    _, store, order, _ = await _seed(test_session, with_lead=False)

    # No exception, no row created — a lead is an acquisition record, not
    # something to invent retroactively from an order.
    await handler_mod.handle_lead_activation_on_order_paid(_paid_event(order, store))

    rows = (await test_session.execute(select(MerchantLeadModel))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_a_missing_store_never_raises(test_session, patched_sessions):
    """Handlers on this bus swallow their failures — the order is already paid."""
    _, store, order, _ = await _seed(test_session)
    event = _paid_event(order, store)
    event.store_id = uuid4()

    await handler_mod.handle_lead_activation_on_order_paid(event)
