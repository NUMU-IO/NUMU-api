"""Who the platform admin sees.

Internal and demo tenants are hidden everywhere outside the Internal tab;
suspended merchants stay listed but leave the queues and live numbers.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from src.application.services.merchant_visibility import live, visible
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)


async def _tenant(session, *, lifecycle="active", internal=False, active=True):
    t = TenantModel(
        id=uuid4(),
        name="T",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="starter",
        lifecycle_state=lifecycle,
        is_internal=internal,
        is_active=active,
    )
    session.add(t)
    await session.flush()
    return t


@pytest.mark.asyncio
async def test_visible_hides_internal_and_demo_but_keeps_suspended(test_session):
    real = await _tenant(test_session)
    suspended = await _tenant(test_session, active=False)
    await _tenant(test_session, internal=True)
    await _tenant(test_session, lifecycle=TenantLifecycleState.DEMO.value)
    await test_session.commit()

    async def ids(cond):
        return set(
            (await test_session.execute(select(TenantModel.id).where(cond))).scalars()
        )

    assert await ids(visible(TenantModel.id)) == {real.id, suspended.id}
    assert await ids(~visible(TenantModel.id)) == (
        await ids(TenantModel.id.isnot(None))
    ) - {real.id, suspended.id}
    assert await ids(live(TenantModel.id)) == {real.id}
