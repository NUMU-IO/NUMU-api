"""Unit tests — first-product and first-commission lead milestones.

``first_order_at`` has its own handler and its own tests. These are the
two either side of it. The behaviour worth pinning is that both are
fill-only: a merchant's second product must not move the date on which
they added their first, because that date is the input to every
time-to-activation number we will ever quote.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import src.infrastructure.events.handlers.lead_milestone_handler as handler_mod
from src.core.events.product_events import ProductCreatedEvent
from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel


@pytest.fixture
def patched_sessions(test_engine, monkeypatch):
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(handler_mod, "AsyncSessionLocal", factory)
    return factory


async def _seed(session, *, with_lead=True, **lead_kw):
    tenant = TenantModel(
        id=uuid4(),
        name="T",
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
    rows = [tenant, store]
    lead = None
    if with_lead:
        lead = MerchantLeadModel(
            email=f"m-{uuid4().hex[:8]}@example.com",
            source="signup",
            status="store_created",
            tenant_id=tenant.id,
            **lead_kw,
        )
        rows.append(lead)
    session.add_all(rows)
    await session.commit()
    return tenant, store, lead


def _product_event(store) -> ProductCreatedEvent:
    return ProductCreatedEvent(
        product_id=uuid4(), store_id=store.id, name="First product", sku="SKU-1"
    )


async def _reload(session, lead_id):
    return (
        await session.execute(
            select(MerchantLeadModel).where(MerchantLeadModel.id == lead_id)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_first_product_stamps_the_lead(test_session, patched_sessions):
    _, store, lead = await _seed(test_session)
    lead_id = lead.id

    await handler_mod.handle_lead_first_product(_product_event(store))

    test_session.expire_all()
    assert (await _reload(test_session, lead_id)).first_product_at is not None


@pytest.mark.asyncio
async def test_second_product_does_not_move_the_date(test_session, patched_sessions):
    """The date is the input to time-to-first-product. It must not drift."""
    original = datetime.now(UTC) - timedelta(days=14)
    _, store, lead = await _seed(test_session, first_product_at=original)
    lead_id = lead.id

    await handler_mod.handle_lead_first_product(_product_event(store))

    test_session.expire_all()
    stored = (await _reload(test_session, lead_id)).first_product_at
    # SQLite returns naive datetimes where Postgres returns aware ones.
    assert (
        abs(
            (
                stored.replace(tzinfo=None) - original.replace(tzinfo=None)
            ).total_seconds()
        )
        < 1
    )


@pytest.mark.asyncio
async def test_tenant_without_a_lead_is_silent(test_session, patched_sessions):
    _, store, _ = await _seed(test_session, with_lead=False)

    await handler_mod.handle_lead_first_product(_product_event(store))

    rows = (await test_session.execute(select(MerchantLeadModel))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_missing_store_never_raises(test_session, patched_sessions):
    """Handlers on this bus swallow failures — the product is already saved."""
    _, store, _ = await _seed(test_session)
    event = _product_event(store)
    event.store_id = uuid4()

    await handler_mod.handle_lead_first_product(event)


@pytest.mark.asyncio
async def test_stamp_returns_false_when_already_set(test_session):
    """The helper reports whether it was the call that set the field.

    The commission handler relies on this to log an insight exactly once
    rather than on every subsequent order.
    """
    tenant, _, lead = await _seed(test_session)

    first = await handler_mod.stamp_lead_milestone(
        test_session, tenant_id=tenant.id, field="first_commission_at"
    )
    second = await handler_mod.stamp_lead_milestone(
        test_session, tenant_id=tenant.id, field="first_commission_at"
    )

    assert first is True
    assert second is False
    assert lead.first_commission_at is not None
