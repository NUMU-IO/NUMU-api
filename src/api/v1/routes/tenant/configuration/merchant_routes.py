"""Merchant-facing configuration request API routes.

These endpoints allow merchants to:
- Create configuration requests for services
- Check configuration status of services
- View their configuration requests
- Cancel pending requests
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.database import get_db
from src.api.dependencies.tenant import get_current_tenant
from src.api.v1.schemas.tenant.configuration import (
    ConfigurationRequestCreate,
    ConfigurationRequestListResponse,
    ConfigurationRequestResponse,
    ConfigurationStatusResponse,
)
from src.application.use_cases.configuration import (
    CancelConfigurationRequestUseCase,
    CreateConfigurationRequestUseCase,
    GetConfigurationStatusUseCase,
    ListConfigurationRequestsUseCase,
)
from src.infrastructure.database.models.tenant.configuration import (
    RequestStatus,
    ServiceName,
    ServiceType,
)

router = APIRouter(
    prefix="/configuration-requests",
    tags=["Configuration Requests"],
)


@router.post(
    "/",
    response_model=ConfigurationRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create configuration request",
    description="""
    Create a new configuration request for a service.

    This notifies administrators that the merchant needs credentials configured
    for a specific service (payment gateway, shipping carrier, etc.).

    **Note**: Only one pending request per service is allowed.
    """,
)
async def create_configuration_request(
    request: ConfigurationRequestCreate,
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db=Depends(get_db),
):
    """Create a new configuration request."""
    use_case = CreateConfigurationRequestUseCase(db)

    try:
        result = await use_case.execute(
            tenant_id=tenant.id,
            user_id=current_user.id,
            service_type=request.service_type,
            service_name=request.service_name,
            notes=request.notes,
            priority=request.priority,
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get(
    "/status/{service_type}/{service_name}",
    response_model=ConfigurationStatusResponse,
    summary="Get configuration status",
    description="""
    Get the configuration status for a specific service.

    Returns whether credentials are configured, validated, and if there
    are any pending configuration requests.
    """,
)
async def get_configuration_status(
    service_type: ServiceType,
    service_name: ServiceName,
    tenant=Depends(get_current_tenant),
    db=Depends(get_db),
):
    """Get configuration status for a service."""
    use_case = GetConfigurationStatusUseCase(db)

    result = await use_case.execute(
        tenant_id=tenant.id,
        service_type=service_type,
        service_name=service_name,
    )
    return result


@router.get(
    "/",
    response_model=ConfigurationRequestListResponse,
    summary="List configuration requests",
    description="Get all configuration requests for the current merchant.",
)
async def list_configuration_requests(
    status_filter: RequestStatus | None = Query(
        None,
        alias="status",
        description="Filter by request status"
    ),
    service_type: ServiceType | None = Query(
        None,
        description="Filter by service type"
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    tenant=Depends(get_current_tenant),
    db=Depends(get_db),
):
    """List all configuration requests for the merchant."""
    use_case = ListConfigurationRequestsUseCase(db)

    result = await use_case.execute(
        tenant_id=tenant.id,
        status_filter=status_filter,
        service_type=service_type,
        page=page,
        page_size=page_size,
    )
    return result


@router.get(
    "/{request_id}",
    response_model=ConfigurationRequestResponse,
    summary="Get configuration request",
    description="Get details of a specific configuration request.",
)
async def get_configuration_request(
    request_id: UUID,
    tenant=Depends(get_current_tenant),
    db=Depends(get_db),
):
    """Get a specific configuration request."""
    use_case = ListConfigurationRequestsUseCase(db)

    result = await use_case.get_by_id(
        tenant_id=tenant.id,
        request_id=request_id,
    )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration request not found"
        )

    return result


@router.delete(
    "/{request_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel configuration request",
    description="""
    Cancel a pending configuration request.

    Only pending requests can be cancelled. Requests that are already
    in progress or completed cannot be cancelled.
    """,
)
async def cancel_configuration_request(
    request_id: UUID,
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db=Depends(get_db),
):
    """Cancel a pending configuration request."""
    use_case = CancelConfigurationRequestUseCase(db)

    try:
        await use_case.execute(
            tenant_id=tenant.id,
            user_id=current_user.id,
            request_id=request_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration request not found"
        )
