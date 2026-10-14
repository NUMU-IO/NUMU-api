"""The live inbox socket lets a super admin in only when their DB staff scope
covers support, so a finance or ops admin can't read any store's messages."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.api.v1.routes.ws.inbox import _user_may_access_store
from src.core.entities.user import UserRole, UserStatus
from src.infrastructure.database import connection
from src.infrastructure.database.models.public.user import UserModel


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "allowed"),
    [
        (None, True),
        ("all", True),
        ("support", True),
        ("finance", False),
        ("ops", False),
    ],
)
async def test_super_admin_needs_a_support_scope(
    test_session, monkeypatch, scope, allowed
):
    admin = UserModel(
        id=uuid4(),
        email=f"a-{uuid4().hex[:8]}@example.com",
        hashed_password="x",
        first_name="A",
        last_name="B",
        role=UserRole.SUPER_ADMIN,
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(UTC),
        admin_scope=scope,
    )
    test_session.add(admin)
    await test_session.flush()

    @asynccontextmanager
    async def _session():
        yield test_session

    monkeypatch.setattr(connection, "AsyncSessionLocal", _session)

    payload = {"user_id": str(admin.id), "role": "super_admin", "tenant_id": None}
    assert await _user_may_access_store(payload, str(uuid4())) is allowed
