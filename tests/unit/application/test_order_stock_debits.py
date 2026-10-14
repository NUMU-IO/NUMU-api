"""Stock debits outside storefront checkout: the variant-level oversell guard,
merchant-created orders, and restock after a partial acceptance."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.dto.order import (
    CreateOrderAddressDTO,
    CreateOrderDTO,
    CreateOrderLineItemDTO,
)
from src.application.services.stock_service import debit_order_line, restock_order
from src.application.use_cases.orders.create_order import CreateOrderUseCase
from src.core.entities.product import ProductStatus
from src.core.exceptions import ValidationError
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.variant import VariantModel


async def _seed(session, *, empty_qty=0, stocked_qty=10, attributes=None):
    tenant_id, store_id = uuid4(), uuid4()
    product = ProductModel(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_id,
        name="Tee",
        slug=f"tee-{uuid4().hex[:6]}",
        status=ProductStatus.ACTIVE,
        quantity=empty_qty + stocked_qty,
        attributes=attributes or {},
    )
    session.add(product)
    empty, stocked = (
        VariantModel(
            id=uuid4(),
            tenant_id=tenant_id,
            store_id=store_id,
            product_id=product.id,
            position=i,
            option_values={"size": size},
            price_amount=25_000,
            price_currency="EGP",
            inventory_quantity=qty,
            track_inventory=True,
        )
        for i, (size, qty) in enumerate((("s", empty_qty), ("m", stocked_qty)))
    )
    session.add_all([empty, stocked])
    await session.flush()
    return SimpleNamespace(
        tenant_id=tenant_id, store_id=store_id, product=product, empty=empty
    )


@pytest.mark.asyncio
async def test_variant_debit_checks_the_variant_not_the_product_total(test_session):
    s = await _seed(test_session)

    ok, reason, _ = await debit_order_line(
        test_session,
        tenant_id=s.tenant_id,
        product_id=s.product.id,
        variant_id=s.empty.id,
        selections=None,
        quantity=1,
    )

    assert (ok, reason) == (False, "insufficient_stock")
    assert s.empty.inventory_quantity == 0


def _create_order(session, s, *, quantity):
    order_repo = AsyncMock()
    order_repo.session = session
    order_repo.get_next_order_number.return_value = "ORD-000001"
    order_repo.create.side_effect = lambda order: order
    store_repo = AsyncMock()
    owner_id = uuid4()
    store_repo.get_by_id.return_value = SimpleNamespace(
        owner_id=owner_id, tenant_id=s.tenant_id
    )
    customer_repo = AsyncMock()
    customer_repo.get_by_id.return_value = SimpleNamespace(store_id=s.store_id)
    use_case = CreateOrderUseCase(order_repo, store_repo, customer_repo)
    dto = CreateOrderDTO(
        customer_id=uuid4(),
        line_items=[
            CreateOrderLineItemDTO(
                product_id=s.product.id,
                product_name="Tee",
                variant_id=s.empty.id,
                quantity=quantity,
                unit_price=25_000,
            )
        ],
        shipping_address=CreateOrderAddressDTO(
            first_name="Mona",
            last_name="Adel",
            address_line1="1 St",
            city="Cairo",
            country="EG",
        ),
    )
    return use_case, dto, owner_id, order_repo


@pytest.mark.asyncio
async def test_merchant_order_debits_stock_and_stamps_the_manifest(test_session):
    s = await _seed(test_session, empty_qty=4)
    use_case, dto, owner_id, order_repo = _create_order(test_session, s, quantity=3)

    await use_case.execute(dto, s.store_id, owner_id)

    assert s.empty.inventory_quantity == 1
    assert s.product.quantity == 11
    order = order_repo.create.call_args.args[0]
    assert order.metadata["stock_debited"]["lines"][0]["quantity"] == 3


@pytest.mark.asyncio
async def test_merchant_draft_does_not_touch_stock(test_session):
    s = await _seed(test_session, empty_qty=4)
    use_case, dto, owner_id, _ = _create_order(test_session, s, quantity=3)

    await use_case.execute(dto, s.store_id, owner_id, as_draft=True)

    assert s.empty.inventory_quantity == 4


@pytest.mark.asyncio
async def test_merchant_order_respects_continue_selling(test_session):
    s = await _seed(test_session)
    use_case, dto, owner_id, _ = _create_order(test_session, s, quantity=1)
    with pytest.raises(ValidationError):
        await use_case.execute(dto, s.store_id, owner_id)

    s2 = await _seed(
        test_session, attributes={"continue_selling_when_out_of_stock": True}
    )
    use_case, dto, owner_id, _ = _create_order(test_session, s2, quantity=1)
    await use_case.execute(dto, s2.store_id, owner_id)
    assert s2.empty.inventory_quantity == -1


@pytest.mark.asyncio
async def test_restock_after_partial_acceptance_returns_only_what_is_out(
    test_session,
):
    s = await _seed(test_session, empty_qty=7)
    line = {"product_id": str(s.product.id), "variant_id": str(s.empty.id)}
    order = SimpleNamespace(
        id=uuid4(),
        tenant_id=s.tenant_id,
        metadata={
            # 3 sold; the customer returned 1 at the door (already restocked).
            "stock_debited": {"lines": [{**line, "quantity": 3}]},
            "stock_restocked_lines": [
                {**line, "quantity": 1, "source": "partial_acceptance"}
            ],
        },
    )

    assert await restock_order(test_session, order, reason="refund_restock_items")

    assert s.empty.inventory_quantity == 9  # +2, not +3
