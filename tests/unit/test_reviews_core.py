"""Core review fixes that came with the Reviews app (docs/Plans/APPS/03-reviews).

A public review never shows an email or a phone as its name; the shopper's
data export lists their reviews; deleting the account keeps the reviews under
a neutral name in the store's language.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Response

from src.application.services.review_moderation import public_reviewer_name
from src.core.entities.product import ProductStatus
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.product_review import (
    ProductReviewModel,
)
from src.infrastructure.repositories.product_review_repository import (
    ProductReviewRepository,
)
from tests.unit import app_platform_rows as rows


@pytest.mark.parametrize(
    ("name", "language", "shown"),
    [
        ("منى سعيد", "ar", "منى سعيد"),
        ("  Mona S  ", "en", "Mona S"),
        ("", "ar", "عميل"),
        (None, "en", "Customer"),
        ("mona@example.com", "en", "Customer"),
        ("+20 101 234 5678", "ar", "عميل"),
        ("01012345678", None, "عميل"),
        ("x" * 200, "en", "x" * 120),
    ],
)
def test_the_public_name_is_never_an_email_a_phone_or_blank(name, language, shown):
    assert public_reviewer_name(name, language) == shown


async def _store_with_reviews(db, language):
    store = await rows.store(db, await rows.user(db))
    store.default_language = language
    customer = CustomerModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        email="mona@example.com",
        phone="01012345678",
        first_name="منى",
        last_name="سعيد",
    )
    product = ProductModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        name="فستان مودال",
        slug="dress",
        status=ProductStatus.ACTIVE,
        quantity=3,
    )
    db.add_all([customer, product])
    await db.flush()
    for approved, rating in ((True, 5), (False, 2)):
        db.add(
            ProductReviewModel(
                id=uuid4(),
                tenant_id=store.tenant_id,
                store_id=store.id,
                product_id=product.id,
                customer_id=customer.id,
                reviewer_name="منى سعيد",
                rating=rating,
                body="الخامة حلوة",
                is_approved=approved,
            )
        )
    await db.flush()
    shopper = SimpleNamespace(
        id=customer.id,
        store_id=store.id,
        email=customer.email,
        phone=customer.phone,
        first_name=customer.first_name,
        last_name=customer.last_name,
        accepts_marketing=False,
        is_verified=False,
        total_orders=1,
        total_spent=0,
        created_at=None,
        notification_preferences={},
    )
    return shopper


async def test_the_export_lists_the_shoppers_reviews(test_session):
    from src.api.v1.routes.storefront import data_rights

    shopper = await _store_with_reviews(test_session, "ar")
    result = await data_rights.data_export(
        shopper,
        SimpleNamespace(),
        SimpleNamespace(get_by_customer=AsyncMock(return_value=[])),
        SimpleNamespace(
            session=test_session, get_by_customer=AsyncMock(return_value=[])
        ),
        ProductReviewRepository(test_session),
    )
    assert sorted(r["rating"] for r in result.data.reviews) == [2, 5]


@pytest.mark.parametrize(("language", "neutral"), [("ar", "عميل"), ("en", "Customer")])
async def test_deleting_the_account_keeps_reviews_under_a_neutral_name(
    test_session, language, neutral
):
    from src.api.v1.routes.storefront import data_rights

    shopper = await _store_with_reviews(test_session, language)
    await data_rights.delete_account(
        Response(),
        shopper,
        SimpleNamespace(session=test_session, update=AsyncMock()),
        SimpleNamespace(get_by_customer=AsyncMock(return_value=[]), delete=AsyncMock()),
    )
    reviews = await ProductReviewRepository(test_session).list_for_customer(shopper.id)
    assert [r.reviewer_name for r in reviews] == [neutral, neutral]
