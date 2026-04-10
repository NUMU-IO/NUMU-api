"""Start a 30-day free trial for a tenant."""

import logging
from datetime import UTC, datetime, timedelta

from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.tenancy.repository import TenantRepository
from src.infrastructure.tenancy.service import TRIAL_LIFETIME_DAYS

logger = logging.getLogger(__name__)


class StartTrialUseCase:
    """Begin a 30-day Trial for an existing tenant. Idempotent."""

    def __init__(self, tenant_repo: TenantRepository) -> None:
        self.tenant_repo = tenant_repo

    async def execute(self, tenant: TenantModel) -> TenantModel:
        if tenant.lifecycle_state == TenantLifecycleState.ACTIVE:
            return tenant
        if tenant.lifecycle_state == TenantLifecycleState.TRIAL:
            return tenant

        now = datetime.now(UTC)
        tenant.lifecycle_state = TenantLifecycleState.TRIAL
        tenant.plan = "trial"
        tenant.trial_started_at = now
        tenant.expires_at = now + timedelta(days=TRIAL_LIFETIME_DAYS)
        tenant.demo_email = None
        tenant.demo_started_at = None
        tenant.read_only_at = None
        tenant.delete_at = None

        await self.tenant_repo.update(tenant)
        logger.info(
            "trial_started",
            extra={
                "tenant_id": str(tenant.id),
                "expires_at": tenant.expires_at.isoformat(),
            },
        )
        return tenant
