"""Unit tests — the platform-level storefront indexing gate.

Guards the fix for the 2026-08-07 Search Console finding: 20 of 36 live
storefronts were synthetic and fully crawlable, producing ~237 junk URLs
(≈ the entire "Discovered – currently not indexed" bucket).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.api.v1.routes.storefront.public import (
    _serialize_public_store,
    platform_indexing_block_reason,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.store import StoreModel


async def _mk_store(session, *, subdomain: str, plan: str = "pro", settings=None):
    owner = UserModel(
        id=uuid4(),
        email=f"{subdomain}-{uuid4().hex[:6]}@example.com",
        hashed_password="x",
        first_name="Store",
        last_name="Owner",
    )
    session.add(owner)
    tenant = TenantModel(
        id=uuid4(),
        name=f"T {subdomain}",
        subdomain=subdomain,
        plan=plan,
        lifecycle_state="active",
        owner_id=owner.id,
    )
    session.add(tenant)
    await session.flush()
    store = StoreModel(
        id=uuid4(),
        tenant_id=tenant.id,
        owner_id=owner.id,
        name=subdomain,
        slug=subdomain,
        subdomain=subdomain,
        settings=settings or {},
    )
    session.add(store)
    await session.commit()
    return store


async def _add_product(session, store, *, status: str = "ACTIVE"):
    product = ProductModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        name="Thing",
        slug=f"thing-{uuid4().hex[:6]}",
        status=status,
    )
    session.add(product)
    await session.commit()
    return product


@pytest.mark.asyncio
async def test_real_store_with_products_is_indexable(test_session):
    store = await _mk_store(test_session, subdomain="realshop")
    await _add_product(test_session, store)

    assert await platform_indexing_block_reason(test_session, store) is None


@pytest.mark.asyncio
async def test_seeded_demo_brand_is_blocked(test_session):
    store = await _mk_store(
        test_session, subdomain="cairo-sound", settings={"demo_seed": "fake-brands-v1"}
    )
    await _add_product(test_session, store)

    assert await platform_indexing_block_reason(test_session, store) == "demo_seed"


@pytest.mark.asyncio
async def test_load_test_store_is_blocked(test_session):
    store = await _mk_store(test_session, subdomain="load-store-7")
    await _add_product(test_session, store)

    assert await platform_indexing_block_reason(test_session, store) == "load_test"


@pytest.mark.asyncio
async def test_demo_plan_tenant_is_blocked(test_session):
    store = await _mk_store(test_session, subdomain="gostav", plan="demo")
    await _add_product(test_session, store)

    assert await platform_indexing_block_reason(test_session, store) == "demo_tenant"


@pytest.mark.asyncio
async def test_empty_store_blocked_then_self_corrects(test_session):
    """A catalogue-less storefront is a soft 404 — but publishing one
    product must make it indexable again with no manual flag flip."""
    store = await _mk_store(test_session, subdomain="brandnew")
    assert await platform_indexing_block_reason(test_session, store) == "no_products"

    # A DRAFT product is not public — still blocked.
    await _add_product(test_session, store, status="DRAFT")
    assert await platform_indexing_block_reason(test_session, store) == "no_products"

    await _add_product(test_session, store, status="ACTIVE")
    assert await platform_indexing_block_reason(test_session, store) is None


@pytest.mark.asyncio
async def test_block_reason_forces_noindex_in_payload(test_session):
    """The gate must land on the exact field the storefront already reads
    (robots.txt -> Disallow, empty sitemap, noindex metadata)."""
    store = await _mk_store(
        test_session, subdomain="fake-brand", settings={"demo_seed": "x"}
    )

    allowed = _serialize_public_store(store, indexing_block_reason=None)
    assert allowed["seo"]["robots_indexing_enabled"] is True
    assert "blocked_reason" not in allowed["seo"]

    blocked = _serialize_public_store(store, indexing_block_reason="demo_seed")
    assert blocked["seo"]["robots_indexing_enabled"] is False
    assert blocked["seo"]["blocked_reason"] == "demo_seed"


@pytest.mark.asyncio
async def test_gate_fails_open(test_session):
    """A broken store object must never de-index a live merchant."""
    broken = SimpleNamespace(settings={}, subdomain="x", id=None, tenant_id=None)
    assert await platform_indexing_block_reason(test_session, broken) is None
