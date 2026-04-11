"""Cancel a tenant's subscription → read-only grace period."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.tenancy.repository import TenantRepository
from src.infrastructure.tenancy.service import TenantService

logger = logging.getLogger(__name__)


class CancelSubscriptionUseCase:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.tenant_service = TenantService(db)

    async def execute(self, tenant_id) -> TenantModel:
        tenant_repo = TenantRepository(self.db)
        tenant = await tenant_repo.get_by_id(tenant_id)
        if not tenant:
            raise ValueError("Tenant not found")

        if tenant.lifecycle_state != TenantLifecycleState.ACTIVE:
            raise ValueError("Only active subscriptions can be cancelled.")

        # TODO: Cancel Paymob recurring billing if paymob_subscription_id exists

        tenant = await self.tenant_service.transition_to_read_only(
            tenant, reason="merchant_cancelled"
        )

        logger.info("subscription_cancelled", extra={"tenant_id": str(tenant_id)})
        return tenant
