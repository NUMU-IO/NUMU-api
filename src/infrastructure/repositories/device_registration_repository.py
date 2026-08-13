"""Device registration repository (Phase 2 — push).

Serves both push clients from one table: the merchant-hub PWA (``webpush``)
and numu-merchant-app (``expo``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.device_registration import (
    DeviceRegistrationModel,
)


class DeviceRegistrationRepository:
    """SQLAlchemy access to ``public.device_registrations``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        endpoint: str,
        provider: str,
        platform: str,
        p256dh: str | None = None,
        auth: str | None = None,
        locale: str | None = None,
        sound: str | None = None,
        user_agent: str | None = None,
    ) -> DeviceRegistrationModel:
        """Create or refresh the row for ``endpoint``.

        Keyed on endpoint, NOT on (user, device): browsers re-issue the same
        endpoint on every ``subscribe()`` call, so keying any other way would
        grow a row per page load. Re-registering also un-revokes and resets
        ``failure_count`` — a merchant who re-enables notifications after a
        revoke is explicitly asking for delivery to resume.
        """
        existing = (
            await self.session.execute(
                select(DeviceRegistrationModel).where(
                    DeviceRegistrationModel.endpoint == endpoint
                )
            )
        ).scalar_one_or_none()

        now = datetime.now(UTC)

        if existing is not None:
            existing.tenant_id = tenant_id
            existing.user_id = user_id
            existing.provider = provider
            existing.platform = platform
            existing.p256dh = p256dh
            existing.auth = auth
            existing.locale = locale
            existing.sound = sound
            existing.user_agent = user_agent
            existing.last_seen_at = now
            existing.failure_count = 0
            existing.revoked_at = None
            return existing

        row = DeviceRegistrationModel(
            tenant_id=tenant_id,
            user_id=user_id,
            endpoint=endpoint,
            provider=provider,
            platform=platform,
            p256dh=p256dh,
            auth=auth,
            locale=locale,
            sound=sound,
            user_agent=user_agent,
            last_seen_at=now,
            failure_count=0,
        )
        self.session.add(row)
        return row

    async def revoke(self, *, user_id: UUID, endpoint: str | None = None) -> int:
        """Soft-revoke one endpoint, or every device for this user.

        Soft rather than hard delete so an audit of "who was notified when"
        stays answerable after a merchant signs out.
        """
        stmt = (
            update(DeviceRegistrationModel)
            .where(
                DeviceRegistrationModel.user_id == user_id,
                DeviceRegistrationModel.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        if endpoint:
            stmt = stmt.where(DeviceRegistrationModel.endpoint == endpoint)

        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def revoke_endpoint(self, endpoint: str) -> None:
        """Revoke after the push service reported the subscription gone (404/410).

        Called from the delivery path, where there is no user context — the
        push service is telling us this endpoint no longer exists at all.
        """
        await self.session.execute(
            update(DeviceRegistrationModel)
            .where(DeviceRegistrationModel.endpoint == endpoint)
            .values(revoked_at=datetime.now(UTC))
        )

    async def record_failure(self, endpoint: str) -> None:
        """Increment the soft-failure counter for a flaky endpoint."""
        await self.session.execute(
            update(DeviceRegistrationModel)
            .where(DeviceRegistrationModel.endpoint == endpoint)
            .values(failure_count=DeviceRegistrationModel.failure_count + 1)
        )

    async def list_active_for_users(
        self, *, tenant_id: UUID, user_ids: list[UUID] | None = None
    ) -> list[DeviceRegistrationModel]:
        """Active registrations for a tenant, optionally narrowed to users.

        Tenant-scoped by construction: a delivery fan-out must never be able to
        reach a device belonging to another merchant.
        """
        query = select(DeviceRegistrationModel).where(
            DeviceRegistrationModel.tenant_id == tenant_id,
            DeviceRegistrationModel.revoked_at.is_(None),
        )
        if user_ids:
            query = query.where(DeviceRegistrationModel.user_id.in_(user_ids))

        result = await self.session.execute(query)
        return list(result.scalars().all())
