"""Regression test — deleting a tenant must not nullify stores.tenant_id.

Prod incident 2026-07-16: the demo-cleanup beat task
(``tasks.cleanup_expired_demo_tenants``) failed every run with
``23502 null value in column "tenant_id" of relation "stores"``.

Root cause: ``TenantModel.stores`` had no ``passive_deletes``, so on
``session.delete(tenant)`` the ORM's default nullify-the-FK behavior
emitted ``UPDATE stores SET tenant_id=NULL`` — but the column is
NOT NULL (DB-level ON DELETE CASCADE is supposed to remove the rows).
The fix is ``passive_deletes="all"`` on the relationship so the ORM
leaves the child rows to the database cascade.

SQLite enforces NOT NULL too, so this test reproduces the prod failure
without Postgres: with the old mapping the flush below raises
IntegrityError; with the fix it succeeds and the store row keeps its
tenant_id (SQLite has FKs off by default, so no cascade fires here —
in Postgres the row is deleted by ON DELETE CASCADE instead).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel


@pytest.mark.asyncio
async def test_tenant_delete_does_not_nullify_store_tenant_id(test_session):
    tenant = TenantModel(
        id=uuid4(),
        name="Demo Tenant",
        subdomain=f"demo-{uuid4().hex[:8]}",
        plan="demo",
        lifecycle_state="expired",
    )
    store = StoreModel(
        id=uuid4(),
        tenant_id=tenant.id,
        owner_id=uuid4(),
        name="Demo Store",
        slug=f"demo-store-{uuid4().hex[:6]}",
        subdomain=f"demo-store-{uuid4().hex[:6]}",
        status="active",
        default_currency="EGP",
        default_language="ar",
        settings={},
        theme_settings={},
        social_links={},
        business_hours={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    test_session.add_all([tenant, store])
    await test_session.commit()

    # Re-fetch so lazy="selectin" populates tenant.stores, exactly like
    # the cleanup task's find_expired_demos() query does.
    fetched = (
        await test_session.execute(
            select(TenantModel).where(TenantModel.id == tenant.id)
        )
    ).scalar_one()
    assert len(fetched.stores) == 1

    # Old mapping: this flush emitted UPDATE stores SET tenant_id=NULL
    # and raised IntegrityError (prod 23502).
    await test_session.delete(fetched)
    await test_session.commit()

    remaining = (
        await test_session.execute(
            select(StoreModel.tenant_id).where(StoreModel.id == store.id)
        )
    ).scalar_one_or_none()
    # SQLite: FK enforcement off → row survives, tenant_id untouched.
    # The assertion that matters is that it was never set to NULL.
    assert remaining == tenant.id
