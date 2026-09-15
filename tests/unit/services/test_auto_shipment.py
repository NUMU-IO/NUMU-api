"""Auto-create shipments: which new orders book, and with which carrier."""

import pytest

import src.application.services.carrier_credentials as carrier_credentials
from src.infrastructure.events.handlers.shipment_handler import (
    auto_create_carrier,
    books_on_creation,
)

CONFIRM_ON_WHATSAPP = {"whatsapp_notifications": {"require_order_confirmation": True}}


@pytest.mark.parametrize(
    ("settings", "method", "status", "expected"),
    [
        ({}, "cod", "pending", True),
        ({}, "COD", "confirmed", True),
        (CONFIRM_ON_WHATSAPP, "cod", "pending", False),
        (CONFIRM_ON_WHATSAPP, "cod", "confirmed", True),
        ({}, "cod", "pending_deposit", False),
        ({}, "card", "pending", False),
        ({}, None, "pending", False),
    ],
)
def test_which_new_orders_book_immediately(settings, method, status, expected):
    assert books_on_creation(settings, method, status) is expected


def test_carrier_needs_auto_create_and_a_connection(monkeypatch):
    monkeypatch.setattr(
        carrier_credentials, "has_credentials", lambda settings, slug: slug == "jt"
    )

    def shipping(entry_by_slug):
        return {"shipping": entry_by_slug}

    assert auto_create_carrier(shipping({"jt": {"auto_create_shipment": True}})) == "jt"
    assert (
        auto_create_carrier(shipping({"jt": {"auto_create_shipment": False}})) is None
    )
    assert (
        auto_create_carrier(shipping({"mylerz": {"auto_create_shipment": True}}))
        is None
    )
    assert (
        auto_create_carrier(
            shipping({"mylerz": {"auto_create_shipment": True, "enabled": True}})
        )
        == "mylerz"
    )
    assert auto_create_carrier(None) is None
