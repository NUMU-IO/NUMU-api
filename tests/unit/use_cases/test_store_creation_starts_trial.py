"""A new store's tenant has to carry the trial, or nothing ever expires.

`expire_trials` selects on ``lifecycle_state = 'trial' AND expires_at < now()``.
Store creation used to call `create_tenant` without either, so every direct
signup produced an ``active`` tenant with a null expiry: measured on
production, 51 of 51 tenants, including 21 merchants whose trial date had
already passed and who were still fully unlocked. The only path that ever
reached the trial lifecycle was a demo conversion.

These pin the two halves the lock depends on — the state and the date — at the
one place a tenant is born.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.dto.store import CreateStoreDTO
from src.application.use_cases.stores.create_store import CreateStoreUseCase


def _use_case():
    """The use case with its three collaborators stubbed.

    Only `create_tenant`'s arguments are under test; the store repository and
    onboarding repository just have to not get in the way.
    """
    tenant_service = AsyncMock()
    tenant_service.create_tenant.return_value = type(
        "T", (), {"id": uuid4(), "subdomain": "shop"}
    )()

    store_repository = AsyncMock()
    store_repository.subdomain_exists.return_value = False
    store_repository.slug_exists.return_value = False
    store_repository.create.side_effect = lambda store: store

    use_case = CreateStoreUseCase(
        store_repository=store_repository,
        tenant_service=tenant_service,
        onboarding_repository=AsyncMock(),
    )
    return use_case, tenant_service


def _dto():
    return CreateStoreDTO(name="Pixel Print", subdomain="pixelprint", slug="pixelprint")


@pytest.mark.asyncio
async def test_a_trialling_signup_gets_a_tenant_that_can_expire():
    use_case, tenant_service = _use_case()
    expires = datetime.now(UTC) + timedelta(days=31)

    try:
        await use_case.execute(
            _dto(), owner_id=uuid4(), plan="trial", trial_expires_at=expires
        )
    except Exception:
        # Everything after the tenant is created (theme seeding, nav menus,
        # onboarding) is out of scope here and stubbed only far enough to
        # reach the assertion below.
        pass

    kwargs = tenant_service.create_tenant.await_args.kwargs
    assert kwargs["lifecycle_state"] == "trial"
    assert kwargs["expires_at"] == expires
    assert kwargs["plan"] == "trial"
    # Without this the trial has no start, and "how long have they had?" can
    # only be answered by guessing from created_at.
    assert kwargs["trial_started_at"] is not None


@pytest.mark.asyncio
async def test_a_store_with_no_trial_is_born_active_with_no_expiry():
    """A merchant whose trial has already lapsed, or who never had one, must
    not get an expiry — an expiry on a non-trial tenant is a lock waiting to
    fire on someone who never agreed to a countdown."""
    use_case, tenant_service = _use_case()

    try:
        await use_case.execute(_dto(), owner_id=uuid4(), plan="free")
    except Exception:
        pass

    kwargs = tenant_service.create_tenant.await_args.kwargs
    assert kwargs["lifecycle_state"] == "active"
    assert kwargs["expires_at"] is None
    assert kwargs["trial_started_at"] is None


def test_the_trial_plan_is_not_the_demo_sandbox():
    """Store creation stamped `plan="demo"` on trialling merchants, which caps
    them at 10 products and 50 orders a month — the Try-a-Demo sandbox limits,
    applied to a real merchant building a real catalogue."""
    from src.core.entities.plan import get_plan_features

    trial = get_plan_features("trial")
    demo = get_plan_features("demo")
    assert trial.max_products > demo.max_products
    assert trial.max_orders_per_month > demo.max_orders_per_month
    assert trial.custom_domain_enabled and not demo.custom_domain_enabled
