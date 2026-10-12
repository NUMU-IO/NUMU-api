"""Rows for the app-platform tests: a store, an app, its OAuth client, an install."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from src.core.entities.app import AppStatus
from src.core.entities.user import UserRole, UserStatus
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
    AppOAuthClientModel,
)
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.store import StoreModel

SECRET = "numu_cs_" + "s" * 43


async def user(s):
    u = UserModel(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        hashed_password="x",
        first_name="Test",
        last_name="User",
        role=UserRole.STORE_OWNER,
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(UTC),
    )
    s.add(u)
    await s.flush()
    return u


async def store(s, owner, *, plan="starter"):
    sub = f"s-{uuid4().hex[:8]}"
    tenant = TenantModel(id=uuid4(), name="T", subdomain=sub, plan=plan)
    s.add(tenant)
    await s.flush()
    row = StoreModel(
        id=uuid4(),
        tenant_id=tenant.id,
        owner_id=owner.id,
        name="Store",
        slug=sub,
        subdomain=sub,
        settings={},
    )
    s.add(row)
    await s.flush()
    return row


async def app(
    s, *, slug=None, developer=None, status=AppStatus.PUBLISHED, embedded=True
):
    row = AppModel(
        id=uuid4(),
        slug=slug or f"app-{uuid4().hex[:6]}",
        name="App",
        developer_id=developer.id if developer else None,
        status=status,
        manifest={
            "app": {
                "app_url": "https://app.example.com",
                "embedded": embedded,
                "embedded_path": "/app",
            }
        },
    )
    s.add(row)
    client = AppOAuthClientModel(
        id=uuid4(),
        app_id=row.id,
        client_id=f"numu_ci_{uuid4().hex[:24]}",
        client_secret_hash="x",
    )
    s.add(client)
    await s.flush()
    return SimpleNamespace(app=row, client_id=client.client_id)


async def install(s, store_row, app_row, *, status="active", enabled=True):
    row = AppInstallationModel(
        id=uuid4(),
        tenant_id=store_row.tenant_id,
        store_id=store_row.id,
        app_id=app_row.id,
        is_enabled=enabled,
        settings={},
        status=status,
    )
    s.add(row)
    await s.flush()
    return row


async def kill_switch(s, *, on: bool):
    s.add(PlatformConfigModel(key="partner_apps", value={"enabled": on}))
    await s.flush()
