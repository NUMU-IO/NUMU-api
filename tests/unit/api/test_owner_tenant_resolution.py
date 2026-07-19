"""Regression tests — owner-tenant resolution for /billing + /wallet.

One user owns MULTIPLE tenants (one per store), so the old
``scalar_one_or_none(owner_id == user)`` raised MultipleResultsFound for
multi-store merchants. The resolver must (a) never crash on multiples,
(b) prefer the middleware-resolved current-store tenant when it belongs
to the caller, and (c) prefer real tenants over demos in the fallback.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.dependencies.tenant_context import resolve_owner_tenant
from src.infrastructure.database.models.public.tenant import TenantModel


def _req(state_tenant=None):
    return SimpleNamespace(state=SimpleNamespace(tenant=state_tenant))


async def _mk_tenant(session, owner_id, plan="free", name="T"):
    tenant = TenantModel(
        id=uuid4(),
        name=name,
        subdomain=f"t-{uuid4().hex[:10]}",
        plan=plan,
        owner_id=owner_id,
        lifecycle_state="active",
    )
    session.add(tenant)
    await session.commit()
    return tenant


@pytest.mark.asyncio
async def test_multi_tenant_owner_does_not_crash(test_session):
    owner = uuid4()
    first = await _mk_tenant(test_session, owner, name="First")
    second = await _mk_tenant(test_session, owner, name="Second")

    # The old scalar_one_or_none() raised MultipleResultsFound here.
    # (Which of the two wins on identical timestamps is unspecified —
    # the guarantee is: no crash, one of the caller's own tenants.)
    resolved = await resolve_owner_tenant(_req(), test_session, owner)
    assert resolved.id in {first.id, second.id}
    assert resolved.owner_id == owner


@pytest.mark.asyncio
async def test_prefers_current_store_tenant_from_middleware(test_session):
    owner = uuid4()
    await _mk_tenant(test_session, owner, name="Other")
    current = await _mk_tenant(test_session, owner, name="Current")

    resolved = await resolve_owner_tenant(
        _req(state_tenant=current), test_session, owner
    )
    assert resolved.id == current.id


@pytest.mark.asyncio
async def test_foreign_state_tenant_is_ignored(test_session):
    owner = uuid4()
    mine = await _mk_tenant(test_session, owner, name="Mine")
    foreign = await _mk_tenant(test_session, uuid4(), name="NotMine")

    # A stale/foreign X-Tenant-Id must never route billing to someone
    # else's tenant — falls back to the caller's own.
    resolved = await resolve_owner_tenant(
        _req(state_tenant=foreign), test_session, owner
    )
    assert resolved.id == mine.id


@pytest.mark.asyncio
async def test_fallback_prefers_real_tenant_over_demo(test_session):
    owner = uuid4()
    real = await _mk_tenant(test_session, owner, plan="payg", name="Real")
    await _mk_tenant(test_session, owner, plan="demo", name="Demo")

    resolved = await resolve_owner_tenant(_req(), test_session, owner)
    assert resolved.id == real.id


@pytest.mark.asyncio
async def test_no_tenant_raises_404(test_session):
    with pytest.raises(HTTPException) as exc:
        await resolve_owner_tenant(_req(), test_session, uuid4())
    assert exc.value.status_code == 404
