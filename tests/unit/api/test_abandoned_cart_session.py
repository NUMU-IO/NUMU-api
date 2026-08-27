"""Abandoned-cart session start + line-item snapshot.

Two merchant-visible bugs on the "Abandoned checkout details" dialog:

* **Timeline lied about when the cart started.** ``cart/track`` deliberately
  stitches a returning shopper's new session onto their existing recoverable
  row (that is what makes cross-device recovery work), so ``created_at`` is
  when the shopper was FIRST ever seen — often weeks earlier. The dialog
  labelled it "Started cart", so a cart opened tonight read "20 Aug".
  ``extra_data.cart_started_at`` is restamped whenever the session
  fingerprint changes and is what the timeline should show.

* **Every line showed "1 × EGP 0".** The storefront reads ``/api/cart``,
  whose ``adaptCart`` whitelist renames ``unit_price`` to ``price`` and drops
  ``total_price``; the cart-track payload read only the backend spellings.
  The API side of that fix is accepting ``image_url`` on the snapshot so the
  merchant sees the products, not just their names.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.api.v1.routes.storefront.checkout import CartTrackLineItem
from src.api.v1.routes.stores.abandoned_checkouts import _cart_started_at
from src.api.v1.schemas.tenant.abandoned_checkout import (
    AbandonedCheckoutLineItem,
)


class _Cart:
    """Only what ``_cart_started_at`` reads."""

    def __init__(self, extra_data):
        self.extra_data = extra_data


# ── cart_started_at ──────────────────────────────────────────────────


def test_returns_the_stamped_session_start():
    started = datetime(2026, 8, 27, 18, 40, tzinfo=UTC)
    cart = _Cart({"cart_started_at": started.isoformat()})

    assert _cart_started_at(cart) == started


def test_missing_stamp_falls_back_to_none():
    """Rows written before this shipped carry no stamp — the hub then falls
    back to created_at rather than rendering an empty timeline row."""
    assert _cart_started_at(_Cart({})) is None
    assert _cart_started_at(_Cart(None)) is None
    assert _cart_started_at(_Cart({"session_fingerprint": "abc"})) is None


def test_corrupt_stamp_does_not_break_the_response():
    """A bad value must degrade to the fallback, never 500 the dialog."""
    assert _cart_started_at(_Cart({"cart_started_at": "not-a-date"})) is None
    assert _cart_started_at(_Cart({"cart_started_at": 12345})) is None


# ── line-item snapshot ───────────────────────────────────────────────


def test_track_payload_carries_price_and_image():
    li = CartTrackLineItem(
        product_name="Elegance - Burgundy",
        quantity=1,
        unit_price=25_000,
        total_price=25_000,
        image_url="https://cdn.numueg.app/p/elegance.webp",
    )

    assert li.unit_price == 25_000
    assert li.image_url == "https://cdn.numueg.app/p/elegance.webp"


def test_response_line_item_exposes_the_image():
    li = AbandonedCheckoutLineItem.model_validate({
        "product_name": "Aria scarf",
        "quantity": 1,
        "unit_price": 25_000,
        "total_price": 25_000,
        "image_url": "https://cdn.numueg.app/p/aria.webp",
    })

    assert li.image_url == "https://cdn.numueg.app/p/aria.webp"


def test_legacy_rows_without_an_image_still_validate():
    """Carts tracked before the storefront sent images must keep loading."""
    li = AbandonedCheckoutLineItem.model_validate({
        "product_name": "Aria scarf",
        "quantity": 1,
        "unit_price": 0,
        "total_price": 0,
    })

    assert li.image_url is None
    assert li.unit_price == 0
