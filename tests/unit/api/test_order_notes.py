"""Bug 9: a cart note reaches the order.

Themes save a note on the cart (empire writes the shopper's chosen size into
it), but the SDK alias dropped it (`hasattr(cart, "note")` on an entity whose
field is `notes`), the cart response had no `note`, and checkout read only its
own `customer_notes` field. So the merchant never saw the note.

ZERO-U-* cover the merge rule; ZERO-K-01/02 the cart alias; ZERO-K-03…05 how
checkout finds the cart note. All in-memory: no Redis, no Postgres.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.v1.routes.storefront import cart_sdk_aliases
from src.api.v1.routes.storefront.cart_sdk_aliases import (
    SdkUpdateItemRequest,
    sdk_update_cart_item,
)
from src.api.v1.routes.storefront.checkout import merge_order_notes, read_cart_note
from src.core.entities.cart import Cart
from src.infrastructure.repositories import cart_repository

STORE = uuid4()
SIZE_NOTE = "• Jeans — المقاس: 32"


# --- Merge rule ------------------------------------------------------------


def test_zero_u_01_cart_note_alone():
    assert merge_order_notes(SIZE_NOTE, None) == SIZE_NOTE
    assert merge_order_notes(SIZE_NOTE, "  ") == SIZE_NOTE


def test_zero_u_02_checkout_that_prefilled_the_cart_note_is_not_doubled():
    request_note = f"{SIZE_NOTE}\nCall before delivery"
    assert merge_order_notes(SIZE_NOTE, request_note) == request_note


def test_zero_u_03_both_notes_cart_first():
    assert (
        merge_order_notes(SIZE_NOTE, "Call before delivery")
        == f"{SIZE_NOTE}\nCall before delivery"
    )


def test_zero_u_04_capped_and_empty():
    merged = merge_order_notes("a" * 800, "b" * 800)
    assert merged is not None and len(merged) == 1000
    assert merged.startswith("a" * 800)
    assert merge_order_notes(None, None) is None
    assert merge_order_notes("", " ") is None


# --- Cart alias ------------------------------------------------------------


class _Carts:
    def __init__(self):
        self.saved: list[Cart] = []

    async def save(self, cart):
        self.saved.append(cart)
        return cart


class _Products:
    async def get_by_ids(self, ids):
        return []


async def test_zero_k_01_cart_update_saves_and_returns_the_note(monkeypatch):
    cart = Cart(session_id=str(uuid4()), store_id=STORE)
    carts = _Carts()

    async def _get_cart_for(_owner):
        return cart

    monkeypatch.setattr(cart_sdk_aliases, "_get_cart_for", _get_cart_for)
    monkeypatch.setattr(cart_sdk_aliases, "_cart_repo", carts)

    response = await sdk_update_cart_item(
        SdkUpdateItemRequest(note=SIZE_NOTE), owner=None, product_repo=_Products()
    )

    assert [c.notes for c in carts.saved] == [SIZE_NOTE]
    assert response.data.note == SIZE_NOTE


def test_zero_k_02_note_over_1000_characters_is_refused():
    # Refused at validation, so FastAPI answers 422 before the handler runs and
    # the cart is never touched.
    with pytest.raises(ValidationError):
        SdkUpdateItemRequest(note="x" * 1001)
    assert SdkUpdateItemRequest(note="x" * 1000).note == "x" * 1000


# --- Checkout finds the cart note -----------------------------------------


def _redis_with(by_customer=None, by_session=None, broken=False):
    class _Repo:
        async def get_by_customer_id(self, customer_id, store_id):
            if broken:
                raise ConnectionError("redis down")
            return (by_customer or {}).get((customer_id, store_id))

        async def get_by_session_id(self, session_id, store_id):
            return (by_session or {}).get((session_id, store_id))

    return _Repo


def _cart(note):
    return SimpleNamespace(notes=note)


async def test_zero_k_03_guest_checkout_reads_the_session_cart(monkeypatch):
    session = str(uuid4())
    monkeypatch.setattr(
        cart_repository,
        "RedisCartRepository",
        _redis_with(by_session={(session, STORE): _cart(SIZE_NOTE)}),
    )

    note = await read_cart_note(uuid4(), session, STORE)

    assert merge_order_notes(note, None) == SIZE_NOTE


async def test_zero_k_04_signed_in_checkout_reads_the_customer_cart(monkeypatch):
    customer = uuid4()
    monkeypatch.setattr(
        cart_repository,
        "RedisCartRepository",
        _redis_with(by_customer={(customer, STORE): _cart(SIZE_NOTE)}),
    )

    note = await read_cart_note(customer, None, STORE)

    assert (
        merge_order_notes(note, "Leave at the door")
        == f"{SIZE_NOTE}\nLeave at the door"
    )


async def test_zero_k_05_no_cart_or_no_redis_keeps_the_request_note(monkeypatch):
    monkeypatch.setattr(cart_repository, "RedisCartRepository", _redis_with())
    assert await read_cart_note(uuid4(), str(uuid4()), STORE) is None

    monkeypatch.setattr(
        cart_repository, "RedisCartRepository", _redis_with(broken=True)
    )
    note = await read_cart_note(uuid4(), None, STORE)
    assert note is None
    assert merge_order_notes(note, "Leave at the door") == "Leave at the door"
