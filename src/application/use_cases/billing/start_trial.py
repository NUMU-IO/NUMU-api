"""Start a free trial for a tenant (length is admin-configurable)."""

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
    """Begin a free Trial for an existing tenant. Idempotent.

    The trial length comes from the admin-controlled signup settings
    (platform_config ``signup_settings``, default 37 days); the legacy
    ``TRIAL_LIFETIME_DAYS`` constant is only the last-resort fallback.
    """

    def __init__(self, tenant_repo: TenantRepository) -> None:
        self.tenant_repo = tenant_repo

    async def execute(self, tenant: TenantModel) -> TenantModel:
        if tenant.lifecycle_state == TenantLifecycleState.ACTIVE:
            return tenant
        if tenant.lifecycle_state == TenantLifecycleState.TRIAL:
            return tenant

        trial_days = TRIAL_LIFETIME_DAYS
        try:
            from src.application.services.signup_settings import (
                get_signup_settings,
            )

            signup = await get_signup_settings(self.tenant_repo.session)
            trial_days = signup.trial_days
        except Exception:  # noqa: BLE001 — settings must never block a trial
            logger.warning("signup_settings_unavailable_using_default")

        now = datetime.now(UTC)
        tenant.lifecycle_state = TenantLifecycleState.TRIAL
        tenant.plan = "trial"
        tenant.trial_started_at = now
        tenant.expires_at = now + timedelta(days=trial_days)
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
