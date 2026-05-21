"""Tenant repository interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from src.infrastructure.database.models.public import TenantModel


class ITenantRepository(ABC):
    """Abstract interface for tenant repository operations.

    Note: Tenant operations always run against the PUBLIC schema,
    not the tenant-specific schemas.
    """

    @abstractmethod
    async def get_by_id(self, tenant_id: UUID) -> TenantModel | None:
        """Get tenant by ID."""
        ...

    @abstractmethod
    async def get_by_subdomain(self, subdomain: str) -> TenantModel | None:
        """Get tenant by subdomain."""
        ...

    @abstractmethod
    async def create(self, **kwargs) -> TenantModel:
        """Create a new tenant record."""
        ...

    @abstractmethod
    async def update(self, tenant: TenantModel) -> TenantModel:
        """Update an existing tenant."""
        ...

    @abstractmethod
    async def deactivate(self, tenant_id: UUID) -> bool:
        """Deactivate a tenant (soft delete)."""
        ...

    @abstractmethod
    async def list_active(self, skip: int = 0, limit: int = 100) -> list[TenantModel]:
        """List all active tenants with pagination."""
        ...

    # ─── Lifecycle sweeper queries (Stream 1.5 + Stream 4.6) ──────────────

    @abstractmethod
    async def find_expired_demos(self, limit: int = 100) -> list[TenantModel]:
        """Find demo tenants whose 7-day window has elapsed."""
        ...

    @abstractmethod
    async def find_expired_trials(self, limit: int = 100) -> list[TenantModel]:
        """Find trial tenants whose 30-day window has elapsed."""
        ...

    @abstractmethod
    async def find_purgeable_read_only(self, limit: int = 100) -> list[TenantModel]:
        """Find read-only tenants past their delete_at deadline."""
        ...
