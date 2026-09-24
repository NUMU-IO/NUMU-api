"""Plan limit FastAPI dependencies.

The limits live in the entitlement catalog (``plan_entitlements``); these keep
the names the store routes already mount.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.application.services.entitlement_service import EntitlementService
from src.core.entities.plan import PLAN_LIMITS, get_plan_features
from src.core.exceptions import PlanLimitExceededError
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel


async def _store_tenant(store_id: UUID, session: AsyncSession) -> TenantModel | None:
    """The store's tenant, or None for an unknown store (the route 404s it)."""
    return await session.scalar(
        select(TenantModel)
        .join(StoreModel, StoreModel.tenant_id == TenantModel.id)
        .where(StoreModel.id == store_id)
    )


def require_product_limit():
    """Refuse a product create past the plan's product limit.

    Usage in a route:
        @router.post("/{store_id}/products", dependencies=[Depends(require_product_limit())])
    """

    async def _check(
        store_id: UUID,
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        tenant = await _store_tenant(store_id, session)
        if tenant is not None:
            await EntitlementService(session).check_quota(tenant, "products")

    return _check


def require_webhook_feature():
    """Block webhook creation on plans without ``webhooks_enabled``.

    Still read from PLAN_LIMITS: whether webhooks fold into ``api_access`` is
    decision D3.
    """

    async def _check(
        store_id: UUID,
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        tenant = await _store_tenant(store_id, session)
        features = get_plan_features(tenant.plan if tenant else "free")
        if not features.webhooks_enabled:
            raise PlanLimitExceededError(
                resource="webhooks",
                limit=0,
                current=0,
                plan=features.display_name,
                feature="webhooks",
                available_via=[
                    key
                    for key, plan in PLAN_LIMITS.items()
                    if plan.webhooks_enabled and plan.monthly_price_piasters > 0
                ],
            )

    return _check
