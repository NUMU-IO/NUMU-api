"""Tenant repository for database operations."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.interfaces.repositories.tenant_repository import ITenantRepository
from src.infrastructure.database.models.public import TenantModel
from src.infrastructure.database.models.public.tenant import TenantLifecycleState


class TenantRepository(ITenantRepository):
    """Repository for managing Tenant records in the public schema."""

    def __init__(self, session: AsyncSession):
        """Initialize with a database session."""
        self.session = session

    async def get_by_id(self, tenant_id: UUID) -> TenantModel | None:
        """Get tenant by ID."""
        query = select(TenantModel).where(TenantModel.id == tenant_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_subdomain(self, subdomain: str) -> TenantModel | None:
        """Get tenant by subdomain."""
        query = select(TenantModel).where(TenantModel.subdomain == subdomain.lower())
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> TenantModel:
        """Create a new tenant."""
        # Ensure subdomain is lowercase
        if "subdomain" in kwargs:
            kwargs["subdomain"] = kwargs["subdomain"].lower()

        tenant = TenantModel(**kwargs)
        self.session.add(tenant)
        await self.session.flush()
        return tenant

    async def update(self, tenant: TenantModel) -> TenantModel:
        """Update an existing tenant."""
        self.session.add(tenant)
        await self.session.flush()
        return tenant

    async def deactivate(self, tenant_id: UUID) -> bool:
        """Deactivate a tenant (soft delete)."""
        tenant = await self.get_by_id(tenant_id)
        if tenant:
            tenant.is_active = False
            await self.session.flush()
            return True
        return False

    async def list_active(self, skip: int = 0, limit: int = 100) -> list[TenantModel]:
        """List all active tenants with pagination."""
        query = (
            select(TenantModel)
            .where(TenantModel.is_active)
            .offset(skip)
            .limit(limit)
            .order_by(TenantModel.created_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_all(self, skip: int = 0, limit: int = 100) -> list[TenantModel]:
        """List all tenants (active and inactive) with pagination."""
        query = (
            select(TenantModel)
            .offset(skip)
            .limit(limit)
            .order_by(TenantModel.created_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    # ─── Lifecycle sweeper queries (Stream 1.5 + Stream 4.6) ──────────────

    async def find_expired_demos(self, limit: int = 100) -> list[TenantModel]:
        """Demo tenants whose 7-day expiry has elapsed.

        Demos do NOT pass through the read-only grace; they are deleted
        outright by the demo cleanup task.
        """
        now = datetime.now(UTC)
        query = (
            select(TenantModel)
            .where(TenantModel.lifecycle_state == TenantLifecycleState.DEMO)
            .where(TenantModel.expires_at.is_not(None))
            .where(TenantModel.expires_at < now)
            .order_by(TenantModel.expires_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def find_expired_trials(self, limit: int = 100) -> list[TenantModel]:
        """Trial tenants whose 30-day expiry has elapsed but have not converted.

        These flip to ``read_only`` (not deleted) — the read-only purge task
        handles deletion 30 days later.
        """
        now = datetime.now(UTC)
        query = (
            select(TenantModel)
            .where(TenantModel.lifecycle_state == TenantLifecycleState.TRIAL)
            .where(TenantModel.expires_at.is_not(None))
            .where(TenantModel.expires_at < now)
            .order_by(TenantModel.expires_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def find_purgeable_read_only(self, limit: int = 100) -> list[TenantModel]:
        """Read-only tenants past their ``delete_at`` deadline."""
        now = datetime.now(UTC)
        query = (
            select(TenantModel)
            .where(TenantModel.lifecycle_state == TenantLifecycleState.READ_ONLY)
            .where(TenantModel.delete_at.is_not(None))
            .where(TenantModel.delete_at < now)
            .order_by(TenantModel.delete_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())
