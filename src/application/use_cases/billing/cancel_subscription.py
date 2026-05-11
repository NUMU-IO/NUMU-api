"""Cancel a tenant's subscription → read-only grace period.

Clears the recurring-billing state (encrypted card token, paymob
subscription id, retry counter) so the renewal Celery task does not
attempt further charges, then delegates to the existing tenant
lifecycle service for the read-only transition.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

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

    async def execute(self, tenant_id: UUID) -> TenantModel:
        tenant_repo = TenantRepository(self.db)
        tenant = await tenant_repo.get_by_id(tenant_id)
        if not tenant:
            raise ValueError("Tenant not found")

        if tenant.lifecycle_state != TenantLifecycleState.ACTIVE:
            raise ValueError("Only active subscriptions can be cancelled.")

        # Stop the recurring billing loop. The Paymob "subscription" is
        # nominal — we charge a saved card token each period — so
        # "cancel" means clearing the stored token and any provider
        # subscription id so the renewal Celery task skips this tenant.
        tenant.paymob_card_token_encrypted = None
        tenant.paymob_subscription_id = None
        tenant.renewal_retry_count = 0
        tenant.cancelled_at = datetime.now(UTC)
        tenant.next_renewal_at = None

        tenant = await self.tenant_service.transition_to_read_only(
            tenant, reason="merchant_cancelled"
        )

        logger.info("subscription_cancelled", extra={"tenant_id": str(tenant_id)})
        return tenant
