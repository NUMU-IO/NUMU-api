"""A customer token works only on its own store's routes.

A customer token is minted for ONE store (`store_id` claim), but the customer
dependencies accepted a token from any store. Store ids and product ids are
public, so a shopper signed in on store A could post a review on store B's
product: `create_product_review` checked the product against the path's store
and loaded the customer by id alone, then saved the review under store B.
RLS does not stop it (`product_reviews` has no policy, and the API connects as
a superuser, which bypasses the others).

Request-level, with fake repositories, so no Postgres is needed. The token is
a real one from the app's own token service, sent as the storefront sends it:
the `customer_access_token` cookie.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import (
    get_customer_repository,
    get_product_repository,
    get_product_review_repository,
    get_store_repository,
)
from src.api.dependencies.auth import get_optional_customer
from src.infrastructure.external_services.token_service import token_service
from src.main import app

STORE_A = uuid4()
STORE_B = uuid4()
PRODUCT_A = uuid4()
PRODUCT_B = uuid4()
CUSTOMER_A = SimpleNamespace(
    id=uuid4(),
    store_id=STORE_A,
    email="alice@example.com",
    full_name="Alice",
    is_verified=True,
)


class _Reviews:
    def __init__(self):
        self.created = []

    async def customer_has_reviewed(self, product_id, customer_id):
        return False

    async def create(self, review):
        review.created_at = datetime.now(UTC)
        self.created.append(review)
        return review


class _Products:
    async def get_by_id(self, product_id):
        owner = {PRODUCT_A: STORE_A, PRODUCT_B: STORE_B}.get(product_id)
        return SimpleNamespace(id=product_id, store_id=owner) if owner else None


class _Stores:
    async def get_by_id(self, store_id):
        if store_id in (STORE_A, STORE_B):
            return SimpleNamespace(id=store_id, tenant_id=uuid4(), settings={})
        return None


class _NoStores:
    async def get_by_id(self, store_id):
        return None


class _Customers:
    async def get_by_id(self, customer_id):
        return CUSTOMER_A if customer_id == CUSTOMER_A.id else None


@pytest.fixture
def reviews():
    return _Reviews()


@pytest.fixture
def client(reviews):
    app.dependency_overrides[get_product_review_repository] = lambda: reviews
    app.dependency_overrides[get_product_repository] = lambda: _Products()
    app.dependency_overrides[get_store_repository] = lambda: _Stores()
    app.dependency_overrides[get_customer_repository] = lambda: _Customers()
    with TestClient(app) as c:
        c.cookies.clear()
        c.cookies.set(
            "customer_access_token",
            token_service.create_customer_access_token(CUSTOMER_A),
        )
        yield c
    app.dependency_overrides.clear()


def _review(client, store, product):
    return client.post(
        f"/api/v1/storefront/store/{store}/products/{product}/reviews",
        json={"rating": 5, "title": "Great", "body": "Loved it"},
    )


def test_zero_s_01_another_stores_customer_cannot_review(client, reviews):
    response = _review(client, STORE_B, PRODUCT_B)

    assert response.status_code == 401, response.text[:200]
    assert reviews.created == []


def test_zero_s_02_own_stores_customer_can_review(client, reviews):
    response = _review(client, STORE_A, PRODUCT_A)

    assert response.status_code == 201, response.text[:200]
    assert [(r.store_id, r.product_id, r.customer_id) for r in reviews.created] == [
        (STORE_A, PRODUCT_A, CUSTOMER_A.id)
    ]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("auth/verify-email", {"code": "123456"}),
        ("auth/resend-verification", None),
        ("coupons/apply", {"coupon_code": "SAVE10", "order_amount": 100}),
    ],
)
def test_zero_s_03_other_store_routes_refuse_the_token(client, path, body):
    response = client.post(f"/api/v1/storefront/store/{STORE_B}/{path}", json=body)

    assert response.status_code == 401, (path, response.text[:200])


def test_zero_s_04_own_store_coupon_preview_gets_past_auth(client):
    # The store lookup is the handler's first step, so a 404 from it proves
    # the customer dependency admitted the token.
    app.dependency_overrides[get_store_repository] = lambda: _NoStores()

    response = client.post(
        f"/api/v1/storefront/store/{STORE_A}/coupons/apply",
        json={"coupon_code": "SAVE10", "order_amount": 100},
    )

    assert response.status_code == 404, response.text[:200]


@pytest.mark.parametrize(
    ("path_store", "expected"),
    [(STORE_A, CUSTOMER_A), (STORE_B, None), (None, CUSTOMER_A)],
)
async def test_optional_customer_is_a_guest_on_another_store(path_store, expected):
    # Checkout, payment proofs and promotions read the shopper this way: a
    # token from another store is a guest there, never that store's customer.
    request = SimpleNamespace(
        cookies={
            "customer_access_token": token_service.create_customer_access_token(
                CUSTOMER_A
            )
        },
        path_params={} if path_store is None else {"store_id": str(path_store)},
    )

    assert await get_optional_customer(request, _Customers()) is expected
