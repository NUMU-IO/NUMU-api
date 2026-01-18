"""Tenant repository interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID


class ITenantRepository(ABC):
    """Abstract interface for tenant repository operations.
    
    Note: Tenant operations always run against the PUBLIC schema,
    not the tenant-specific schemas.
    """

    @abstractmethod
    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        """Get tenant by ID."""
        ...

    @abstractmethod
    async def get_by_subdomain(self, subdomain: str) -> Tenant | None:
        """Get tenant by subdomain."""
        ...

    @abstractmethod
    async def get_by_schema_name(self, schema_name: str) -> Tenant | None:
        """Get tenant by database schema name."""
        ...

    @abstractmethod
    async def create(self, **kwargs) -> Tenant:
        """Create a new tenant record."""
        ...

    @abstractmethod
    async def update(self, tenant: Tenant) -> Tenant:
        """Update an existing tenant."""
        ...

    @abstractmethod
    async def deactivate(self, tenant_id: UUID) -> bool:
        """Deactivate a tenant (soft delete)."""
        ...

    @abstractmethod
    async def list_active(self, skip: int = 0, limit: int = 100) -> list[Tenant]:
        """List all active tenants with pagination."""
        ...

from src.infrastructure.database.models.public import Tenant
