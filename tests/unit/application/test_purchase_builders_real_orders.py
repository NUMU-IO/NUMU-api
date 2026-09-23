"""The purchase payload builders must accept every order shape their callers pass.

Sweeps, status handlers, ``/track`` enrichment and Fawry pass the ORM
``OrderModel``; the payment and courier webhooks pass the domain ``Order``.
Both used to raise inside the builders (and every caller swallowed it), so no
order-based Purchase reached Meta or TikTok. The older tests passed because
they substituted a ``SimpleNamespace``.
"""

import uuid
from datetime import UTC, datetime

import pytest

from src.application.services import meta_capi_purchase_dispatcher as meta
from src.application.services import tiktok_capi_purchase_dispatcher as tiktok
from src.core.entities.order import Order, OrderLineItem, OrderShippingAddress
from src.infrastructure.database.models.tenant.order import OrderModel

TTCLID = "E.C.P." + "x" * 400
SHIP = {
    "first_name": "Sara",
    "last_name": "Ali",
    "address_line1": "1 Nile St",
    "city": "Cairo",
    "country": "EG",
    "phone": "+201000000000",
}
SNAPSHOT = {
    "ip_address": "197.1.2.3",
    "user_agent": "Mozilla/5.0",
    "ttclid": TTCLID,
    "ttp": "ttp-1",
    "fbp": "fb.1.1.1",
}
PRODUCT_ID = uuid.uuid4()


def _model() -> OrderModel:
    return OrderModel(
        id=uuid.uuid4(),
        store_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        order_number="ORD-000001",
        line_items=[
            {"product_id": str(PRODUCT_ID), "quantity": 2, "unit_price": 15000}
        ],
        shipping_address=dict(SHIP),
        extra_data=dict(SNAPSHOT),
        total=30000,
        currency="EGP",
        session_fingerprint="fp-1",
        paid_at=datetime.now(UTC),
    )


def _entity() -> Order:
    return Order(
        id=uuid.uuid4(),
        store_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        order_number="ORD-000002",
        line_items=[
            OrderLineItem(
                product_id=PRODUCT_ID,
                product_name="Thing",
                quantity=2,
                unit_price=15000,
            )
        ],
        shipping_address=OrderShippingAddress(**SHIP),
        subtotal=30000,
        total=30000,
        currency="EGP",
        metadata=dict(SNAPSHOT),
        session_fingerprint="fp-1",
        paid_at=datetime.now(UTC),
    )


@pytest.mark.parametrize("make", [_model, _entity], ids=["OrderModel", "Order"])
@pytest.mark.parametrize(
    "build",
    [meta._build_user_data_from_order, tiktok._build_user_data_from_order],
    ids=["meta", "tiktok"],
)
def test_user_data_carries_the_checkout_snapshot(make, build):
    user = build(make())

    assert user["phone"] == SHIP["phone"]
    assert user["city"] == "Cairo"
    assert user["ip"] == "197.1.2.3"
    assert user["user_agent"] == "Mozilla/5.0"
    assert user["external_id"] == "fp-1"
    if build is tiktok._build_user_data_from_order:
        assert user["ttclid"] == TTCLID


@pytest.mark.parametrize("make", [_model, _entity], ids=["OrderModel", "Order"])
@pytest.mark.parametrize(
    "build",
    [meta._build_custom_data_from_order, tiktok._build_custom_data_from_order],
    ids=["meta", "tiktok"],
)
def test_custom_data_carries_the_line_items(make, build):
    data = build(make())

    assert data["value"] == 300
    assert data["content_ids"] == [str(PRODUCT_ID)]
    assert data["contents"][0]["quantity"] == 2
    assert data["num_items"] == 2
