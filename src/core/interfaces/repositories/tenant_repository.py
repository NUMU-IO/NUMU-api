"""Tenant repository interface."""

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID


class ITenantRepository(ABC):
    """Abstract interface for tenant repository operations.
    
    Note: Tenant operations always run against the PUBLIC schema,
    not the tenant-specific schemas.
    """

    @abstractmethod
    async def get_by_id(self, tenant_id: UUID) -> Optional["Tenant"]:
        """Get tenant by ID."""
        ...

    @abstractmethod
    async def get_by_subdomain(self, subdomain: str) -> Optional["Tenant"]:
        """Get tenant by subdomain."""
        ...

    @abstractmethod
    async def get_by_schema_name(self, schema_name: str) -> Optional["Tenant"]:
        """Get tenant by database schema name."""
        ...

    @abstractmethod
    async def create(self, **kwargs) -> "Tenant":
        """Create a new tenant record."""
        ...

    @abstractmethod
    async def update(self, tenant: "Tenant") -> "Tenant":
        """Update an existing tenant."""
        ...

    @abstractmethod
    async def deactivate(self, tenant_id: UUID) -> bool:
        """Deactivate a tenant (soft delete)."""
        ...

    @abstractmethod
    async def list_active(self, skip: int = 0, limit: int = 100) -> list["Tenant"]:
        """List all active tenants with pagination."""
        ...


# Avoid circular import - use string annotation for Tenant
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.tenants.models import Tenant
