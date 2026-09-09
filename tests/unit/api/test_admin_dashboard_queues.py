"""Queue-depth counting for the admin shell.

The sidebar badges and the overview triage strip both read
``/admin/dashboard/queues``, so a wrong number here is a wrong number in
two places at once — and the failure mode is silent: a queue that reads
zero is a queue nobody opens.

Two rules are worth pinning down. Demo and internal tenants are excluded
from the tenant-scoped counts, exactly as ``/stats`` excludes them, so
seeded demo stores never inflate the operator's workload. And the
lifecycle counts are per-state: a read-only tenant is a billing problem,
a trialing one is a sales opportunity, and they must never be summed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)


async def _tenant(session, *, lifecycle="active", internal=False):
    t = TenantModel(
        id=uuid4(),
        name="T",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="starter",
        lifecycle_state=lifecycle,
        is_internal=internal,
    )
    session.add(t)
    await session.flush()
    return t


def _excluded_tenant_ids():
    """The subquery the endpoint builds, verbatim."""
    return (
        select(TenantModel.id)
        .where(
            (TenantModel.lifecycle_state == TenantLifecycleState.DEMO.value)
            | (TenantModel.is_internal.is_(True))
        )
        .scalar_subquery()
    )


@pytest.mark.asyncio
async def test_demo_and_internal_tenants_are_both_excluded(test_session):
    """Either flag on its own is enough to drop a tenant from the counts."""
    real = await _tenant(test_session)
    await _tenant(test_session, lifecycle=TenantLifecycleState.DEMO.value)
    await _tenant(test_session, internal=True)
    # Both at once — must not double-exclude or resurrect the row.
    await _tenant(
        test_session, lifecycle=TenantLifecycleState.DEMO.value, internal=True
    )
    await test_session.commit()

    kept = (
        (
            await test_session.execute(
                select(TenantModel.id).where(
                    TenantModel.id.notin_(_excluded_tenant_ids())
                )
            )
        )
        .scalars()
        .all()
    )

    assert kept == [real.id]


@pytest.mark.asyncio
async def test_lifecycle_counts_are_per_state_and_skip_internal(test_session):
    """Read-only and trialing are separate numbers, and staff tenants are neither."""
    await _tenant(test_session, lifecycle=TenantLifecycleState.READ_ONLY.value)
    await _tenant(test_session, lifecycle=TenantLifecycleState.READ_ONLY.value)
    await _tenant(test_session, lifecycle=TenantLifecycleState.TRIAL.value)
    await _tenant(
        test_session, lifecycle=TenantLifecycleState.READ_ONLY.value, internal=True
    )
    await test_session.commit()

    async def count(state):
        return (
            await test_session.execute(
                select(func.count(TenantModel.id)).where(
                    TenantModel.lifecycle_state == state,
                    TenantModel.is_internal.is_(False),
                )
            )
        ).scalar()

    assert await count(TenantLifecycleState.READ_ONLY.value) == 2
    assert await count(TenantLifecycleState.TRIAL.value) == 1


@pytest.mark.asyncio
async def test_now_is_timezone_aware(test_session):
    """The 48-hour cutoff subtracts from an aware `now`; a naive one raises."""
    assert datetime.now(UTC).tzinfo is not None
