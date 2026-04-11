"""Tenant lifecycle guard dependencies.

These FastAPI dependencies enforce that a tenant is in the right
lifecycle state before allowing state-mutating operations.
"""

from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.infrastructure.database.models.public.tenant import TenantModel


async def require_writable_tenant(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TenantModel:
    """Ensure the current user's tenant can accept mutations.

    Raises HTTP 402 Payment Required if the tenant is in read_only
    or cancelled state. Returns the tenant model for downstream use.

    Usage: add ``tenant: TenantModel = Depends(require_writable_tenant)``
    to any route that creates orders, products, customers, or shipments.
    """
    q = select(TenantModel).where(TenantModel.owner_id == user_id)
    result = await db.execute(q)
    tenant = result.scalar_one_or_none()

    if not tenant:
        # No tenant found — let the request proceed (the route will
        # fail on its own when it tries to find the store).
        return None  # type: ignore[return-value]

    if tenant.is_writable:
        return tenant

    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "error": "tenant_read_only",
            "message": "اشتراكك انتهى — جدد عشان تستلم أوردرات جديدة",
            "message_en": "Your subscription has expired. Renew to accept new orders.",
            "renew_url": "/billing",
            "lifecycle_state": tenant.lifecycle_state,
        },
    )
