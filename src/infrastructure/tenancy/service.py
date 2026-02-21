"""Tenant service for tenant lifecycle management.

All tenant data lives in the shared 'public' PostgreSQL schema with a
tenant_id discriminator column and RLS enforcement. No per-tenant schemas
are created — isolation is handled entirely by Row-Level Security.
"""

import hashlib
import logging
import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.tenancy.repository import TenantRepository

logger = logging.getLogger(__name__)


class TenantService:
    """Service for managing tenant registration and lifecycle."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.tenant_repo = TenantRepository(db)

    async def create_tenant(
        self,
        name: str,
        subdomain: str,
        owner_id: UUID = None,
        plan: str = "free",
        is_active: bool = True,
    ):
        """Create a new tenant record.

        Args:
            name: Display name for the store
            subdomain: Unique subdomain (e.g., 'mystore' for mystore.numu.io)
            owner_id: UUID of the user who owns this store
            plan: Subscription plan (free, pro, enterprise)

        Returns:
            Created Tenant object

        Raises:
            ValueError: If subdomain is invalid or already exists
        """
        # Validate subdomain format
        if not self._validate_subdomain(subdomain):
            raise ValueError(
                f"Invalid subdomain '{subdomain}'. Must be 3-63 characters, "
                "lowercase alphanumeric with hyphens, cannot start/end with hyphen."
            )

        # Check for existing subdomain
        existing = await self.tenant_repo.get_by_subdomain(subdomain)
        if existing:
            raise ValueError(f"Subdomain '{subdomain}' already exists")

        # Generate a stable identifier for this tenant (used in settings/logs)
        schema_name = self._generate_schema_name(subdomain)

        # Create tenant record in public schema
        tenant = await self.tenant_repo.create(
            name=name,
            subdomain=subdomain,
            owner_id=owner_id,
            plan=plan,
            is_active=is_active,
            settings={"schema_name": schema_name},
        )

        logger.info(f"Created tenant '{subdomain}' (id={tenant.id})")
        return tenant

    def _validate_subdomain(self, subdomain: str) -> bool:
        """Validate subdomain format (RFC 1123 compliant)."""
        if not subdomain or len(subdomain) < 3 or len(subdomain) > 63:
            return False
        # Must be lowercase alphanumeric with hyphens, no start/end with hyphen
        pattern = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
        return bool(re.match(pattern, subdomain.lower()))

    def _generate_schema_name(self, subdomain: str) -> str:
        """Generate a stable tenant identifier string.

        This is stored in tenant.settings['schema_name'] for logging and
        identification. No actual PostgreSQL schema is created — all data
        lives in the public schema with RLS enforcement.
        """
        safe_subdomain = subdomain.lower().replace("-", "_")
        schema_hash = hashlib.md5(
            subdomain.encode(), usedforsecurity=False
        ).hexdigest()[:8]
        return f"tenant_{safe_subdomain}_{schema_hash}"
