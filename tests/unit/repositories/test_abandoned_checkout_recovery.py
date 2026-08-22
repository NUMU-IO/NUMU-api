"""Abandoned-checkout repository — the three defects behind an empty hub list.

1. ``find_active_for_session`` must accept the ``Email`` value object the
   core Customer entity carries (asyncpg could not bind it → cart_track
   threw for every OTP-verified shopper, silently).
2. ``list_by_store`` total must not be inflated by a cartesian product.
3. ``mark_recovered_for_shopper`` must not sweep months-old carts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

import src.infrastructure.repositories.abandoned_checkout_repository as repo_module
from src.core.value_objects.email import Email
from src.infrastructure.database.models.tenant.abandoned_checkout import (
    AbandonedCheckoutModel,
)
from src.infrastructure.repositories.abandoned_checkout_repository import (
    AbandonedCheckoutRepository,
)


def _row(store_id, tenant_id, *, email=None, phone=None, days_ago=0, fingerprint=None):
    ts = datetime.now(UTC) - timedelta(days=days_ago)
    return AbandonedCheckoutModel(
        id=uuid4(),
        store_id=store_id,
        tenant_id=tenant_id,
        customer_id=None,
        email=email,
        phone=phone,
        line_items=[{"product_id": str(uuid4()), "quantity": 1, "unit_price": 100}],
        subtotal=100,
        shipping_cost=0,
        tax_amount=0,
        discount_amount=0,
        total=100,
        currency="EGP",
        last_activity_at=ts,
        created_at=ts,
        updated_at=ts,
        extra_data={"session_fingerprint": fingerprint} if fingerprint else {},
    )


@pytest.mark.asyncio
async def test_find_active_accepts_email_value_object(test_session):
    store, tenant = uuid4(), uuid4()
    test_session.add(_row(store, tenant, email="Yahia@Example.com"))
    await test_session.commit()

    repo = AbandonedCheckoutRepository(test_session)
    found = await repo.find_active_for_session(
        store_id=store,
        session_fingerprint=None,
        email=Email(value="yahia@example.com"),  # VO, not str
    )
    assert found is not None
    assert found.email == "Yahia@Example.com"


@pytest.mark.asyncio
async def test_list_total_is_not_a_cartesian_product(test_session, monkeypatch):
    store, tenant = uuid4(), uuid4()
    for i in range(4):
        test_session.add(_row(store, tenant, phone=f"+2010000000{i}"))
    await test_session.commit()

    # Engage _tenant_filter (the trigger). sqlite can't compare a UUID
    # column to the str the contextvar holds, so hand it the UUID directly.
    monkeypatch.setattr(repo_module, "get_tenant_id", lambda: tenant)
    repo = AbandonedCheckoutRepository(test_session)
    items, total = await repo.list_by_store(
        store, skip=0, limit=2, abandoned_only=False
    )
    assert len(items) == 2
    assert total == 4  # was 16 (4 × 4) with the filter on the outer COUNT


@pytest.mark.asyncio
async def test_recovery_only_attributes_recent_carts(test_session):
    store, tenant = uuid4(), uuid4()
    recent = _row(store, tenant, phone="+2010012", days_ago=2)
    stale = _row(store, tenant, phone="+2010012", days_ago=45)
    test_session.add_all([recent, stale])
    await test_session.commit()

    repo = AbandonedCheckoutRepository(test_session)
    # Exact-phone path only: the last-9-digits clause uses regexp_replace,
    # which sqlite lacks. The recency bound is what this test pins.
    n = await repo.mark_recovered_for_shopper(
        store_id=store,
        session_fingerprint=None,
        email=None,
        phone="+2010012",
        order_id=uuid4(),
    )
    await test_session.commit()
    assert n == 1
    await test_session.refresh(recent)
    await test_session.refresh(stale)
    assert recent.recovered_at is not None
    assert stale.recovered_at is None
