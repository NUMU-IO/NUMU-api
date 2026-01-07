"""Tenant repository for database operations."""

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.interfaces.repositories.tenant_repository import ITenantRepository
from src.infrastructure.database.models.public import Tenant


class TenantRepository(ITenantRepository):
    """Repository for managing Tenant records in the public schema."""
    
    def __init__(self, session: AsyncSession):
        """Initialize with a database session.
        
        Note: The session should be configured to query the PUBLIC schema
        since tenants table is always in public.
        """
        self.session = session

    async def get_by_id(self, tenant_id: UUID) -> Optional[Tenant]:
        """Get tenant by ID."""
        query = select(Tenant).where(Tenant.id == tenant_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_subdomain(self, subdomain: str) -> Optional[Tenant]:
        """Get tenant by subdomain."""
        query = select(Tenant).where(Tenant.subdomain == subdomain.lower())
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_by_schema_name(self, schema_name: str) -> Optional[Tenant]:
        """Get tenant by schema name."""
        query = select(Tenant).where(Tenant.schema_name == schema_name)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> Tenant:
        """Create a new tenant."""
        # Ensure subdomain is lowercase
        if 'subdomain' in kwargs:
            kwargs['subdomain'] = kwargs['subdomain'].lower()
        
        tenant = Tenant(**kwargs)
        self.session.add(tenant)
        await self.session.flush()
        return tenant
    
    async def update(self, tenant: Tenant) -> Tenant:
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
    
    async def list_active(self, skip: int = 0, limit: int = 100) -> list[Tenant]:
        """List all active tenants with pagination."""
        query = (
            select(Tenant)
            .where(Tenant.is_active == True)
            .offset(skip)
            .limit(limit)
            .order_by(Tenant.created_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())
