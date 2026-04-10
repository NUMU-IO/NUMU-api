"""Tenant service for tenant lifecycle management.

All tenant data lives in the shared 'public' PostgreSQL schema with a
tenant_id discriminator column and RLS enforcement. No per-tenant schemas
are created — isolation is handled entirely by Row-Level Security.
"""

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.tenancy.repository import TenantRepository

logger = logging.getLogger(__name__)


# Demo tenants live for 7 days. Trial tenants live for 30 days. Both then
# enter the read-only state (trials only) for another 30 days before deletion.
DEMO_LIFETIME_DAYS = 7
TRIAL_LIFETIME_DAYS = 30
READ_ONLY_GRACE_DAYS = 30


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
        plan: str = "trial",
        is_active: bool = True,
        lifecycle_state: str = TenantLifecycleState.ACTIVE,
        expires_at: datetime | None = None,
        demo_email: str | None = None,
        demo_started_at: datetime | None = None,
        trial_started_at: datetime | None = None,
    ):
        """Create a new tenant record.

        Args:
            name: Display name for the store
            subdomain: Unique subdomain (e.g., 'mystore' for mystore.numu.io)
            owner_id: UUID of the user who owns this store
            plan: Subscription plan (trial, starter, pro, enterprise, demo)
            is_active: Soft-delete flag
            lifecycle_state: Initial state in the lifecycle state machine
            expires_at: When this tenant should be auto-cleaned (demo/trial)
            demo_email: Captured email for the Try-a-Demo flow
            demo_started_at: When the demo session started
            trial_started_at: When the 30-day trial began

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
            lifecycle_state=lifecycle_state,
            expires_at=expires_at,
            demo_email=demo_email,
            demo_started_at=demo_started_at,
            trial_started_at=trial_started_at,
        )

        logger.info(
            "tenant_created",
            extra={
                "subdomain": subdomain,
                "tenant_id": str(tenant.id),
                "lifecycle_state": lifecycle_state,
                "plan": plan,
            },
        )
        return tenant

    # ─── Lifecycle state transitions ──────────────────────────────────────

    async def transition_to_read_only(
        self,
        tenant: TenantModel,
        reason: str,
    ) -> TenantModel:
        """Transition a tenant to the read-only grace state.

        Used when a trial expires without conversion, or when a paying
        merchant cancels their subscription, or when dunning gives up
        after the final retry. The tenant has ``READ_ONLY_GRACE_DAYS``
        before it is purged.
        """
        now = datetime.now(UTC)
        tenant.lifecycle_state = TenantLifecycleState.READ_ONLY
        tenant.read_only_at = now
        tenant.delete_at = now + timedelta(days=READ_ONLY_GRACE_DAYS)
        tenant.expires_at = None  # cleared so the trial sweeper stops picking it up
        await self.tenant_repo.update(tenant)
        logger.info(
            "tenant_transition_read_only",
            extra={
                "tenant_id": str(tenant.id),
                "reason": reason,
                "delete_at": tenant.delete_at.isoformat(),
            },
        )
        return tenant

    async def transition_to_active(self, tenant: TenantModel) -> TenantModel:
        """Activate a paying tenant.

        Called by ``SubscribeUseCase`` when a trial → paid conversion
        succeeds, or by the read-only-to-active path when a previously
        cancelled merchant resubscribes.
        """
        now = datetime.now(UTC)
        tenant.lifecycle_state = TenantLifecycleState.ACTIVE
        tenant.expires_at = None
        tenant.read_only_at = None
        tenant.delete_at = None
        if not tenant.trial_converted_at:
            tenant.trial_converted_at = now
        await self.tenant_repo.update(tenant)
        logger.info("tenant_transition_active", extra={"tenant_id": str(tenant.id)})
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
