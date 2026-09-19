"""`POST /abandoned-checkouts/{id}/convert` builds a real order from the cart.

Regression: "Mark recovered" only flipped `recovered_at`, so the hub showed a
Recovered cart with no order anywhere (Vionne, 2026-09-18).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.v1.routes.stores import abandoned_checkouts as mod
from src.core.entities.abandoned_checkout import AbandonedCheckout


def _checkout(store_id, **kw):
    base = dict(  # noqa: C408
        store_id=store_id,
        phone="+201068960688",
        line_items=[
            {
                "product_id": str(uuid4()),
                "product_name": "Elegance - Bronze",
                "variant_id": str(uuid4()),
                "variant_name": "Bronze",
                "sku": None,
                "quantity": 2,
                "unit_price": 25000,
                "total_price": 50000,
                "image_url": "https://cdn.example/x.jpg",
            }
        ],
        shipping_address={
            "first_name": "ندا",
            "last_name": "محمد",
            "address_line1": "6 اكتوبر الحي السادس",
            "city": "6 اكتوبر",
            "state": "Giza",
            "country": "EG",
            "phone": "+201068960688",
        },
        shipping_cost=6000,
        discount_amount=5000,
    )
    base.update(kw)
    return AbandonedCheckout(**base)


@pytest.mark.asyncio
async def test_convert_creates_cod_order_and_links_it(monkeypatch):
    store = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), owner_id=uuid4())
    checkout = _checkout(store.id)
    order_id = uuid4()

    repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=checkout),
        mark_recovered=AsyncMock(return_value=checkout),
    )
    customer = SimpleNamespace(id=uuid4())
    customer_repo = SimpleNamespace(
        get_by_phone=AsyncMock(return_value=None),
        get_by_email=AsyncMock(return_value=None),
        create=AsyncMock(return_value=customer),
    )
    create_order = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(id=order_id))
    )
    monkeypatch.setattr("src.api.v1.routes.stores.orders.create_order", create_order)
    # Live offer + shipping figures, not the row's stale 6000 / 5000.
    reprice = AsyncMock(return_value=(8000, 10000))
    monkeypatch.setattr(mod, "_reprice", reprice)

    await mod.convert_abandoned_checkout(
        checkout_id=checkout.id,
        store=store,
        repo=repo,
        order_repo=None,
        store_repo=None,
        customer_repo=customer_repo,
        onboarding_repo=None,
        network_repo=None,
        product_repo=None,
        shipping_repo=None,
        coupon_repo=None,
        promotion_repo=None,
        promotion_target_repo=None,
        promotion_event_repo=None,
    )

    req = create_order.await_args.kwargs["request"]
    assert req.customer_id == customer.id
    assert req.payment_method == "cod"
    assert req.shipping_cost == 8000 and req.discount_amount == 10000
    assert req.line_items[0].quantity == 2 and req.line_items[0].unit_price == 25000
    assert req.shipping_address.address_line1 == "6 اكتوبر الحي السادس"
    repo.mark_recovered.assert_awaited_once_with(checkout.id, order_id=order_id)


@pytest.mark.asyncio
async def test_convert_refuses_a_cart_that_already_has_an_order():
    store = SimpleNamespace(id=uuid4())
    checkout = _checkout(store.id, recovered_order_id=uuid4())
    repo = SimpleNamespace(get_by_id=AsyncMock(return_value=checkout))
    with pytest.raises(mod.HTTPException) as exc:
        await mod.convert_abandoned_checkout(
            checkout_id=checkout.id,
            store=store,
            repo=repo,
            order_repo=None,
            store_repo=None,
            customer_repo=None,
            onboarding_repo=None,
            network_repo=None,
            product_repo=None,
            shipping_repo=None,
            coupon_repo=None,
            promotion_repo=None,
            promotion_target_repo=None,
            promotion_event_repo=None,
        )
    assert exc.value.status_code == 409


def test_governorate_names_in_cart_rows_resolve():
    # Rows store the checkout form's governorate ("Giza"); _reprice needs a code.
    from src.core.value_objects.geography import resolve_governorate

    assert resolve_governorate("Giza").code == "EG-GZ"
    assert resolve_governorate("الجيزة").code == "EG-GZ"
