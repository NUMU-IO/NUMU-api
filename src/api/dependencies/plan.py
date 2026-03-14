"""Plan limit FastAPI dependencies.

Import these into route functions to enforce plan limits before write operations.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.application.services.plan_limit_service import PlanLimitService
from src.infrastructure.repositories import StoreRepository


async def _get_store_tenant(
    store_id: UUID,
    session: AsyncSession,
) -> UUID:
    """Resolve store_id → tenant_id."""
    store_repo = StoreRepository(session)
    store = await store_repo.get_by_id(store_id)
    if not store or not store.tenant_id:
        return UUID(int=0)  # unknown tenant: limit service will fall back to free
    return store.tenant_id


async def get_plan_limit_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PlanLimitService:
    """Dependency that provides a PlanLimitService bound to the current DB session."""
    return PlanLimitService(session)


# ------------------------------------------------------------------
# Reusable limit-check dependencies
# ------------------------------------------------------------------


def require_product_limit(store_id_param: str = "store_id"):
    """Returns a FastAPI dependency that checks the product limit for the given store.

    Usage in a route:
        @router.post("/{store_id}/products", dependencies=[Depends(require_product_limit())])
    """

    async def _check(
        store_id: UUID,
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        tenant_id = await _get_store_tenant(store_id, session)
        await PlanLimitService(session).check_product_limit(store_id, tenant_id)

    return _check


def require_order_limit(store_id_param: str = "store_id"):
    """Returns a FastAPI dependency that checks the monthly order limit."""

    async def _check(
        store_id: UUID,
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        tenant_id = await _get_store_tenant(store_id, session)
        await PlanLimitService(session).check_order_limit(store_id, tenant_id)

    return _check


def require_webhook_feature():
    """Returns a FastAPI dependency that blocks webhook creation on plans that don't support it."""

    async def _check(
        store_id: UUID,
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        tenant_id = await _get_store_tenant(store_id, session)
        await PlanLimitService(session).require_webhooks(tenant_id)

    return _check


def require_discount_feature():
    """Returns a FastAPI dependency that blocks discount creation on plans that don't support it."""

    async def _check(
        store_id: UUID,
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        tenant_id = await _get_store_tenant(store_id, session)
        await PlanLimitService(session).require_discount_codes(tenant_id)

    return _check
