"""Tenant dependencies for FastAPI routes."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from src.api.dependencies.auth import get_current_user_id
from src.infrastructure.database.models.public import TenantModel


async def get_current_tenant(request: Request) -> TenantModel:
    """
    Get the current tenant from the request state.
    
    This dependency extracts the tenant that was set by TenantMiddleware.
    Use this in routes that require tenant context.
    
    Raises:
        HTTPException: If no tenant context is available.
    
    Returns:
        TenantModel: The current tenant object.
    """
    tenant = getattr(request.state, "tenant", None)
    
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant context not available. Ensure you're accessing via a valid subdomain.",
        )
    
    return tenant


async def get_optional_tenant(request: Request) -> TenantModel | None:
    """
    Get the current tenant from request state, or None if not available.
    
    Use this in routes that can work both with and without tenant context.
    
    Returns:
        TenantModel | None: The current tenant or None.
    """
    return getattr(request.state, "tenant", None)


def require_tenant_owner():
    """
    Dependency factory that requires the current user to be the tenant owner.
    
    Usage:
        @router.put("/settings")
        async def update_settings(
            tenant: Annotated[TenantModel, Depends(get_current_tenant)],
            user_id: Annotated[UUID, Depends(get_current_user_id)],
            _: Annotated[None, Depends(require_tenant_owner())],
        ):
            ...
    """
    async def check_ownership(
        tenant: Annotated[TenantModel, Depends(get_current_tenant)],
        user_id: Annotated[UUID, Depends(get_current_user_id)],
    ) -> None:
        if tenant.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to perform this action on this store.",
            )
    
    return check_ownership


# Type alias for dependency injection
CurrentTenant = Annotated[TenantModel, Depends(get_current_tenant)]
OptionalTenant = Annotated[TenantModel | None, Depends(get_optional_tenant)]
