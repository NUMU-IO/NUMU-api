"""Unit tests for the new RETURNED order status.

Covers the SHIPPED → RETURNED transition added so manual-ship merchants
can record a customer-refused (RTO) outcome that feeds the cross-merchant
trust network.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.entities.order import (
    VALID_STATUS_TRANSITIONS,
    Order,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)


def _build_order(*, status: OrderStatus = OrderStatus.PENDING) -> Order:
    return Order(
        store_id=uuid4(),
        customer_id=uuid4(),
        order_number="ORD-1000",
        line_items=[],
        shipping_address=OrderShippingAddress(
            first_name="Test",
            last_name="Customer",
            address_line1="1 Test St",
            city="Cairo",
            country="EG",
        ),
        status=status,
        payment_status=PaymentStatus.PENDING,
        subtotal=10_000,
        shipping_cost=0,
        tax_amount=0,
        discount_amount=0,
        total=10_000,
        currency="EGP",
        payment_method="cod",
    )


def test_returned_status_exists_and_is_lowercase_value():
    assert OrderStatus.RETURNED.value == "returned"


def test_shipped_can_transition_to_returned():
    transitions = VALID_STATUS_TRANSITIONS[OrderStatus.SHIPPED]
    assert OrderStatus.RETURNED in transitions
    assert OrderStatus.DELIVERED in transitions  # No regression


def test_returned_is_terminal():
    assert VALID_STATUS_TRANSITIONS[OrderStatus.RETURNED] == []


def test_return_to_origin_transitions_shipped_order():
    order = _build_order(status=OrderStatus.SHIPPED)
    order.return_to_origin(reason="Customer refused at door")
    assert order.status == OrderStatus.RETURNED
    assert order.cancelled_at is not None  # Reuses cancelled_at timestamp
    assert order.metadata.get("return_reason") == "Customer refused at door"


def test_return_to_origin_records_status_history():
    order = _build_order(status=OrderStatus.SHIPPED)
    order.return_to_origin(reason="Refused")
    history = order.metadata.get("status_history") or []
    assert len(history) == 1
    assert history[0]["from"] == "shipped"
    assert history[0]["to"] == "returned"
    assert history[0]["reason"] == "Refused"


def test_return_to_origin_blocked_from_pending():
    order = _build_order(status=OrderStatus.PENDING)
    with pytest.raises(ValueError, match="Invalid status transition"):
        order.return_to_origin()


def test_return_to_origin_blocked_from_delivered():
    order = _build_order(status=OrderStatus.DELIVERED)
    with pytest.raises(ValueError, match="Invalid status transition"):
        order.return_to_origin()


def test_returned_order_cannot_transition_anywhere():
    order = _build_order(status=OrderStatus.SHIPPED)
    order.return_to_origin()
    # Any subsequent transition should fail — RETURNED is terminal.
    with pytest.raises(ValueError, match="Invalid status transition"):
        order.transition_to(OrderStatus.DELIVERED)
    with pytest.raises(ValueError, match="Invalid status transition"):
        order.transition_to(OrderStatus.REFUNDED)


def test_address_geolocation_fields_round_trip():
    """Defends against the regression where lat/lng/source were silently
    stripped on persistence, breaking the trust check's teleport detection."""
    from src.infrastructure.repositories.order_repository import OrderRepository

    address = OrderShippingAddress(
        first_name="Test",
        last_name="Customer",
        address_line1="1 Test St",
        city="Cairo",
        country="EG",
        phone="+201001234567",
        latitude=30.0444,
        longitude=31.2357,
        location_accuracy=15.0,
        location_source="gps",
        geocoded_address="1 Test St, Cairo, Egypt",
    )

    # Use the helpers directly; no DB session needed.
    repo = OrderRepository.__new__(OrderRepository)
    serialized = repo._address_to_dict(address)
    rehydrated = repo._dict_to_address(serialized)

    assert rehydrated.latitude == 30.0444
    assert rehydrated.longitude == 31.2357
    assert rehydrated.location_accuracy == 15.0
    assert rehydrated.location_source == "gps"
    assert rehydrated.geocoded_address == "1 Test St, Cairo, Egypt"


def test_legacy_address_dict_without_geolocation_loads_as_none():
    """Orders written before the geolocation fix load with None coords."""
    from src.infrastructure.repositories.order_repository import OrderRepository

    legacy_dict = {
        "first_name": "Test",
        "last_name": "Customer",
        "address_line1": "1 Test St",
        "city": "Cairo",
        "country": "EG",
        "phone": "+201001234567",
    }
    repo = OrderRepository.__new__(OrderRepository)
    rehydrated = repo._dict_to_address(legacy_dict)

    assert rehydrated.latitude is None
    assert rehydrated.longitude is None
    assert rehydrated.location_source is None
