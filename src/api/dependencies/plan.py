"""Plan limit FastAPI dependencies.

The limits live in the entitlement catalog (``plan_entitlements``); these keep
the names the store routes already mount.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.application.services.entitlement_service import (
    EntitlementService,
    tenant_for_store,
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
        tenant = await tenant_for_store(session, store_id)
        if tenant is not None:
            await EntitlementService(session).check_quota(tenant, "products")

    return _check
