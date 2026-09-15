"""Unit tests for the storefront newsletter signup (POST /newsletter/subscribe).

A signup is a customer row with accepts_marketing=True and the ``newsletter``
tag. The response must not reveal whether the email was already a customer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.middleware.rate_limit import _is_newsletter_subscribe
from src.api.v1.routes.storefront.public import (
    NewsletterSubscribeRequest,
    subscribe_newsletter,
)
from src.core.entities.customer import Customer
from src.core.exceptions import EntityNotFoundError
from src.core.value_objects.email import Email


def _repos(store, existing=None):
    customer_repo = SimpleNamespace(
        get_by_email=AsyncMock(return_value=existing),
        create=AsyncMock(),
        update=AsyncMock(),
    )
    store_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=store))
    return customer_repo, store_repo


async def _call(store_id, body, customer_repo, store_repo):
    return await subscribe_newsletter(
        store_id=store_id,
        body_in=NewsletterSubscribeRequest(**body),
        customer_repo=customer_repo,
        store_repo=store_repo,
    )


@pytest.mark.asyncio
async def test_new_email_creates_marketing_customer():
    store_id, tenant_id = uuid4(), uuid4()
    customer_repo, store_repo = _repos(
        SimpleNamespace(id=store_id, tenant_id=tenant_id)
    )

    res = await _call(
        store_id, {"email": "Sara@Example.com"}, customer_repo, store_repo
    )

    assert res.data == {"status": "subscribed"}
    created = customer_repo.create.await_args
    customer = created.args[0]
    assert str(customer.email) == "sara@example.com"
    assert customer.accepts_marketing is True
    assert customer.tags == ["newsletter"]
    assert created.kwargs["tenant_id"] == tenant_id
    customer_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_customer_is_opted_in_and_tagged():
    store_id = uuid4()
    existing = Customer(
        store_id=store_id,
        email=Email(value="sara@example.com"),
        first_name="Sara",
        last_name="A",
        tags=["vip"],
    )
    customer_repo, store_repo = _repos(
        SimpleNamespace(id=store_id, tenant_id=uuid4()), existing
    )

    res = await _call(
        store_id, {"email": "sara@example.com"}, customer_repo, store_repo
    )

    assert res.data == {"status": "subscribed"}
    customer_repo.create.assert_not_awaited()
    updated = customer_repo.update.await_args.args[0]
    assert updated.accepts_marketing is True
    assert updated.tags == ["vip", "newsletter"]
    assert updated.first_name == "Sara"


@pytest.mark.asyncio
async def test_already_subscribed_writes_nothing():
    store_id = uuid4()
    existing = Customer(
        store_id=store_id,
        email=Email(value="sara@example.com"),
        first_name="",
        last_name="",
        accepts_marketing=True,
        tags=["newsletter"],
    )
    customer_repo, store_repo = _repos(
        SimpleNamespace(id=store_id, tenant_id=uuid4()), existing
    )

    res = await _call(
        store_id, {"email": "sara@example.com"}, customer_repo, store_repo
    )

    assert res.data == {"status": "subscribed"}
    customer_repo.create.assert_not_awaited()
    customer_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_honeypot_answers_the_same_and_writes_nothing():
    store_id = uuid4()
    customer_repo, store_repo = _repos(SimpleNamespace(id=store_id, tenant_id=uuid4()))

    res = await _call(
        store_id,
        {"email": "bot@example.com", "website": "http://spam.example"},
        customer_repo,
        store_repo,
    )

    assert res.data == {"status": "subscribed"}
    customer_repo.get_by_email.assert_not_awaited()
    customer_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_store_is_404():
    customer_repo, store_repo = _repos(None)
    with pytest.raises(EntityNotFoundError):
        await _call(uuid4(), {"email": "sara@example.com"}, customer_repo, store_repo)
    customer_repo.create.assert_not_awaited()


def test_newsletter_path_gets_its_own_rate_limit_tier():
    assert _is_newsletter_subscribe(
        f"/api/v1/storefront/store/{uuid4()}/newsletter/subscribe"
    )
    assert not _is_newsletter_subscribe(f"/api/v1/storefront/store/{uuid4()}/checkout")
