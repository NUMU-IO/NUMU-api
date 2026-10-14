"""Admin phone lookup: one buyer across every real merchant.

Support reads the return rate off this to judge a COD order, so the math
is pinned: returned / (delivered + returned), demo stores left out, and a
local-format phone finds the E.164 rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.v1.routes.admin.customers import lookup_phone
from src.core.entities.order import OrderStatus, PaymentStatus
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel

PHONE = "+201001234567"
D, R, C = OrderStatus.DELIVERED, OrderStatus.RETURNED, OrderStatus.CANCELLED
P, U = PaymentStatus.PAID, PaymentStatus.PENDING


async def _buyer(session, *, lifecycle="active", orders=()):
    tenant = TenantModel(
        id=uuid4(),
        name="T",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="starter",
        lifecycle_state=lifecycle,
    )
    store = StoreModel(
        id=uuid4(),
        tenant_id=tenant.id,
        owner_id=uuid4(),
        name=f"Store {lifecycle}",
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
    customer = CustomerModel(
        id=uuid4(),
        tenant_id=tenant.id,
        store_id=store.id,
        email=f"{uuid4().hex[:6]}@x.io",
        first_name="Ali",
        last_name="Hassan",
        phone=PHONE,
    )
    rows = [
        OrderModel(
            id=uuid4(),
            tenant_id=tenant.id,
            store_id=store.id,
            customer_id=customer.id,
            order_number=f"ORD-{uuid4().hex[:6]}",
            status=status,
            payment_status=payment,
            fulfillment_status="UNFULFILLED",
            subtotal=total,
            total=total,
            currency="EGP",
            shipping_address={},
        )
        for status, payment, total in orders
    ]
    session.add_all([tenant, store, customer, *rows])
    await session.commit()


@pytest.mark.asyncio
async def test_stats_span_real_merchants_only(test_session):
    await _buyer(
        test_session,
        orders=[(D, P, 5000), (R, U, 700)],
    )
    await _buyer(
        test_session,
        orders=[(D, P, 3000), (C, U, 900)],
    )
    await _buyer(test_session, lifecycle="demo", orders=[(R, P, 1)])

    data = (await lookup_phone(uuid4(), test_session, "01001234567")).data

    assert data.phone == PHONE
    assert len(data.customers) == 2
    s = data.stats
    assert (s.orders, s.stores, s.delivered, s.returned, s.cancelled) == (
        4,
        2,
        2,
        1,
        1,
    )
    assert s.paid_total_cents == 8000
    assert s.return_rate == pytest.approx(1 / 3)
    assert len(data.orders) == 4


@pytest.mark.asyncio
async def test_no_delivered_or_returned_means_no_rate(test_session):
    await _buyer(test_session, orders=[(OrderStatus.PENDING, U, 100)])
    data = (await lookup_phone(uuid4(), test_session, PHONE)).data
    assert data.stats.return_rate is None


@pytest.mark.asyncio
async def test_garbage_phone_is_422(test_session):
    with pytest.raises(HTTPException) as exc:
        await lookup_phone(uuid4(), test_session, "not-a-phone")
    assert exc.value.status_code == 422
