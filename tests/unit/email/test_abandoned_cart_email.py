"""The cart-recovery email has to carry the cart, and a way back to it.

What shipped before was four unstyled paragraphs: no recipient name, no
pictures, no prices, and no link — it asked the shopper to come back and gave
them no way to do it. Every field it needed was already in the cart snapshot.
"""

from __future__ import annotations

from src.infrastructure.external_services.resend.email_templates.abandoned_cart import (
    abandoned_cart_email_html,
)

ITEMS = [
    {
        "product_name": "Elegance",
        "variant_name": "Burgundy",
        "quantity": 2,
        "unit_price": 120_000,
        "total_price": 240_000,
        "image_url": "https://cdn.example/elegance.jpg",
    },
    {"product_name": "Aria scarf", "quantity": 1, "unit_price": 35_000},
]


def _render(**overrides):
    kwargs = {
        "store_name": "vionne",
        "recovery_url": "https://vionne.numueg.app/api/cart/recover?cart=c-1",
        "line_items": ITEMS,
        "total_cents": 275_000,
        "currency": "EGP",
        "customer_name": "Yahia",
        "language": "en",
    }
    kwargs.update(overrides)
    return abandoned_cart_email_html(**kwargs)


def test_the_cart_is_in_the_email():
    _, html = _render()
    assert "Elegance" in html and "Burgundy" in html
    assert "Aria scarf" in html
    assert "https://cdn.example/elegance.jpg" in html
    # Line total, not unit price: two at 1,200 is 2,400.
    assert "2,400.00" in html
    assert "2,750.00" in html  # cart total


def test_there_is_a_way_back_to_the_cart():
    """The one thing the old email lacked entirely."""
    _, html = _render()
    assert html.count("https://vionne.numueg.app/api/cart/recover?cart=c-1") >= 1


def test_a_line_with_no_picture_still_renders():
    """A cart line whose product has no image must not leave a broken box."""
    _, html = _render(line_items=[{"product_name": "Aria scarf", "quantity": 1}])
    assert "Aria scarf" in html
    assert "No image" in html


def test_the_shopper_is_greeted_by_name_when_we_know_it():
    _, named = _render()
    assert "Hi Yahia" in named

    # And is not greeted by a blank when we don't.
    _, anon = _render(customer_name=None)
    assert "Hi 👋" in anon
    assert "Hi  " not in anon


def test_arabic_renders_right_to_left_with_arabic_copy():
    subject, html = _render(language="ar")
    assert "سلتك" in subject
    assert 'dir="rtl"' in html
    assert "كمّل طلبك" in html  # the CTA
    # Money stays LTR inside an RTL document, or the amount reads backwards.
    assert "direction:ltr" in html


def test_an_empty_cart_does_not_raise():
    """Defensive: a snapshot can be emptied by the shopper between the last
    track call and the merchant pressing send."""
    subject, html = _render(line_items=[], total_cents=0)
    assert subject
    assert "0.00" in html
