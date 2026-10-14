"""Staff hard delete (``DELETE /admin/orders/{id}``).

Deleting an order money was taken against would strand that money, its
invoice and its commission, so only an unpaid order with no deposit and no
recorded payment goes. Stock comes back only if the order never shipped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.api.v1.routes.admin.orders import delete_order
from src.application.services import stock_service
from src.core.entities.instapay import PaymentProofStatus
from src.core.entities.order import FulfillmentStatus, OrderStatus, PaymentStatus
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.payment_proof import PaymentProofModel
from src.infrastructure.database.models.tenant.store import StoreModel


async def _order(session, **fields):
    tenant = TenantModel(
        id=uuid4(),
        name="T",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="starter",
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
    customer = CustomerModel(
        id=uuid4(),
        tenant_id=tenant.id,
        store_id=store.id,
        email=f"{uuid4().hex[:6]}@x.io",
        first_name="Ali",
        last_name="Hassan",
    )
    order = OrderModel(
        id=uuid4(),
        tenant_id=tenant.id,
        store_id=store.id,
        customer_id=customer.id,
        order_number=f"ORD-{uuid4().hex[:6]}",
        status=fields.pop("status", OrderStatus.PENDING),
        payment_status=fields.pop("payment_status", PaymentStatus.PENDING),
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        subtotal=1000,
        total=1000,
        currency="EGP",
        shipping_address={},
        **fields,
    )
    session.add_all([tenant, store, customer, order])
    await session.commit()
    return order


@pytest.fixture
def restock(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(stock_service, "restock_order", mock)
    return mock


async def _gone(session, order_id):
    return (
        await session.scalar(select(OrderModel.id).where(OrderModel.id == order_id))
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [
        {"payment_status": PaymentStatus.PAID},
        {"payment_status": PaymentStatus.AUTHORIZED},
        {"payment_status": PaymentStatus.PARTIALLY_REFUNDED},
        {"deposit_paid_at": datetime.now(UTC)},
    ],
)
async def test_money_on_the_order_blocks_delete(test_session, restock, fields):
    order = await _order(test_session, **fields)
    with pytest.raises(HTTPException) as exc:
        await delete_order(order.id, uuid4(), test_session)
    assert exc.value.status_code == 409
    assert not await _gone(test_session, order.id)


@pytest.mark.asyncio
async def test_a_recorded_part_payment_blocks_delete(test_session, restock):
    order = await _order(test_session)
    test_session.add(
        PaymentProofModel(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            order_id=order.id,
            proof_image_key="",
            proof_image_hash=b"x",
            transaction_ref="cash-1",
            declared_amount_cents=500,
            status=PaymentProofStatus.APPROVED,
            recorded_method="cash",
        )
    )
    await test_session.commit()
    with pytest.raises(HTTPException) as exc:
        await delete_order(order.id, uuid4(), test_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "restocks"),
    [(OrderStatus.PENDING, True), (OrderStatus.SHIPPED, False)],
)
async def test_unpaid_order_is_deleted_and_restocked_only_if_unshipped(
    test_session, restock, status, restocks
):
    order = await _order(test_session, status=status)
    await delete_order(order.id, uuid4(), test_session)
    assert await _gone(test_session, order.id)
    assert restock.await_count == (1 if restocks else 0)
