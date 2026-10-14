"""Staff trial extension (``POST /admin/merchants/{id}/extend-trial``).

The trial expiry sweep moves an unconverted trial to read_only and arms the
purge (``delete_at``). Extending such a trial has to undo exactly that, or the
merchant is "extended" while still locked and still scheduled for deletion.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.api.v1.routes.admin import merchant_ops
from src.infrastructure.database.models.audit import AuditLogModel
from src.infrastructure.database.models.public.tenant import TenantModel


async def _tenant(session, **fields):
    t = TenantModel(
        id=uuid4(), name="T", subdomain=f"t-{uuid4().hex[:8]}", plan="trial", **fields
    )
    session.add(t)
    await session.flush()
    return t


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.setattr(
        merchant_ops.WalletService, "invalidate_cache", AsyncMock(return_value=None)
    )


@pytest.mark.asyncio
async def test_expired_trial_goes_back_to_trial_and_off_the_purge(test_session):
    now = datetime.now(UTC)
    t = await _tenant(
        test_session,
        lifecycle_state="read_only",
        read_only_at=now,
        delete_at=now + timedelta(days=30),
    )
    admin = uuid4()

    await merchant_ops.extend_trial(
        t.id,
        merchant_ops.ExtendTrialIn(days=7, reason="sales call"),
        admin_id=admin,
        db=test_session,
    )

    assert t.lifecycle_state == "trial"
    assert t.read_only_at is None and t.delete_at is None
    assert timedelta(days=6.9) < t.expires_at - now < timedelta(days=7.1)
    log = (await test_session.execute(select(AuditLogModel))).scalar_one()
    assert log.event_type == "billing.trial_extended"
    assert log.user_id == admin
    assert log.details["actor_type"] == "staff"


@pytest.mark.asyncio
async def test_live_trial_extends_from_its_current_end(test_session):
    end = datetime.now(UTC) + timedelta(days=2)
    t = await _tenant(test_session, lifecycle_state="trial", expires_at=end)

    await merchant_ops.extend_trial(
        t.id,
        merchant_ops.ExtendTrialIn(days=5, reason="more time"),
        admin_id=uuid4(),
        db=test_session,
    )

    assert t.expires_at == end + timedelta(days=5)


@pytest.mark.asyncio
async def test_converted_read_only_tenant_is_refused(test_session):
    """Cancelled or dunned subscribers are read_only too, but never trials."""
    now = datetime.now(UTC)
    t = await _tenant(
        test_session,
        lifecycle_state="read_only",
        trial_converted_at=now - timedelta(days=60),
        delete_at=now + timedelta(days=10),
    )

    with pytest.raises(HTTPException) as exc:
        await merchant_ops.extend_trial(
            t.id,
            merchant_ops.ExtendTrialIn(days=5, reason="nope"),
            admin_id=uuid4(),
            db=test_session,
        )

    assert exc.value.status_code == 409
    assert t.lifecycle_state == "read_only"
