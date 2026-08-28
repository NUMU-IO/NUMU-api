"""The TikTok CompletePayment's identity match keys.

These are the keys TikTok joins a conversion back to the ad click with. The
Meta sibling (``meta_capi_purchase_dispatcher``) had already been corrected on
both counts; the TikTok dispatcher was left behind, which meant the server-side
conversion — the one that survives ad blockers and iOS, and the one TikTok
optimises spend against — carried strictly less identity than Meta's copy of
the same order. A merchant reading the two Events Managers side by side sees
that as TikTok "losing" conversions Meta claims.

Covered here:

* ``email`` — ``OrderShippingAddress`` has no email field, so reading
  ``shipping["email"]`` was a permanent None. It must come from the customer
  record instead.
* ``external_id`` — TikTok's ``_first_external_id`` falls back to this key
  when there is no customer id. Guest COD checkout is the majority of MENA
  orders, so omitting it meant most conversions had no external id at all.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from src.application.services.tiktok_capi_purchase_dispatcher import (
    _build_user_data_from_order,
)

FINGERPRINT = "01J8ZQ4M0GDT4W2CJH8N6Y7X5R"


def _order(**overrides):
    base = {
        "id": uuid4(),
        "store_id": uuid4(),
        "customer_id": None,
        "session_fingerprint": FINGERPRINT,
        # A real order's shipping address. Note there is no `email` key — the
        # value object does not define one, which is the whole point.
        "shipping_address": {
            "phone": "+201234567890",
            "first_name": "Sara",
            "last_name": "Ali",
            "city": "Cairo",
            "country": "EG",
            "postal_code": "11511",
        },
        "line_items": [],
        "total": 25_000,
        "currency": "EGP",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestExternalId:
    def test_guest_order_still_carries_the_session_fingerprint(self):
        """The join that guest COD depends on."""
        ud = _build_user_data_from_order(_order())
        assert ud["customer_id"] is None
        assert ud["external_id"] == FINGERPRINT

    def test_customer_id_and_external_id_coexist(self):
        cid = uuid4()
        ud = _build_user_data_from_order(_order(customer_id=cid))
        assert ud["customer_id"] == str(cid)
        assert ud["external_id"] == FINGERPRINT

    def test_missing_fingerprint_degrades_to_none_not_an_error(self):
        order = _order()
        del order.session_fingerprint
        assert _build_user_data_from_order(order)["external_id"] is None


class TestEmail:
    def test_email_is_left_for_the_customer_lookup_to_fill(self):
        """Never read from the shipping address — it has no such field."""
        assert _build_user_data_from_order(_order())["email"] is None

    def test_a_stray_email_on_the_address_is_not_trusted(self):
        """Legacy/imported orders can carry one; the customer row is the
        source of truth, and `fill_identity_from_customer` only fills a blank
        — so an address value here would win and could not be corrected."""
        order = _order(
            shipping_address={"phone": "+201234567890", "email": "stale@old.example"}
        )
        assert _build_user_data_from_order(order)["email"] is None


class TestUnchangedKeys:
    """The fix must not disturb the keys that were already right."""

    def test_address_derived_keys_survive(self):
        ud = _build_user_data_from_order(_order())
        assert ud["phone"] == "+201234567890"
        assert ud["first_name"] == "Sara"
        assert ud["last_name"] == "Ali"
        assert ud["city"] == "Cairo"
        assert ud["zip"] == "11511"
        assert ud["country_code"] == "eg"

    def test_click_ids_come_from_the_metadata_snapshot(self):
        order = _order()
        order.metadata = {
            "ttclid": "TT_CLICK_1",
            "ttp": "TTP_1",
            "ip_address": "197.blah",
            "user_agent": "UA/1.0",
        }
        ud = _build_user_data_from_order(order)
        assert ud["ttclid"] == "TT_CLICK_1"
        assert ud["ttp"] == "TTP_1"
        assert ud["ip"] == "197.blah"
        assert ud["user_agent"] == "UA/1.0"
