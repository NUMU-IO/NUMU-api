"""Admin-facing credential configuration API routes.

These endpoints allow administrators to:
- View all pending configuration requests
- Configure credentials for merchants
- Validate credentials before storing
- Manage credential lifecycle
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies.auth import require_admin, require_super_admin
from src.api.dependencies.database import get_db
from src.api.v1.schemas.tenant.configuration import (
    ConfigurationRequestListResponse,
    ConfigurationRequestResponse,
    ConfigurationRequestUpdate,
    CredentialConfigureRequest,
    CredentialStatusResponse,
    CredentialValidateRequest,
    ServiceInfoResponse,
    SupportedServicesResponse,
)
from src.api.v1.schemas.tenant.configuration.credential_schemas import (
    CredentialValidationResponse,
)
from src.application.use_cases.configuration import (
    ConfigureCredentialsUseCase,
    GetSupportedServicesUseCase,
    ListAllConfigurationRequestsUseCase,
    RevokeCredentialsUseCase,
    UpdateConfigurationRequestUseCase,
    ValidateCredentialsUseCase,
)
from src.infrastructure.database.models.tenant.configuration import (
    RequestStatus,
    ServiceName,
    ServiceType,
)

router = APIRouter(
    prefix="/admin/credentials",
    tags=["Admin - Credentials"],
)


@router.get(
    "/pending-requests",
    response_model=ConfigurationRequestListResponse,
    summary="List pending configuration requests",
    description="Get all pending configuration requests across all merchants.",
)
async def list_pending_requests(
    status_filter: RequestStatus | None = Query(
        RequestStatus.PENDING, alias="status", description="Filter by request status"
    ),
    service_type: ServiceType | None = Query(
        None, description="Filter by service type"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin=Depends(require_admin),
    db=Depends(get_db),
):
    """List all pending configuration requests."""
    use_case = ListAllConfigurationRequestsUseCase(db)

    result = await use_case.execute(
        status_filter=status_filter,
        service_type=service_type,
        page=page,
        page_size=page_size,
    )
    return result


@router.get(
    "/requests/{request_id}",
    response_model=ConfigurationRequestResponse,
    summary="Get configuration request details",
    description="Get details of a specific configuration request.",
)
async def get_request_details(
    request_id: UUID,
    admin=Depends(require_admin),
    db=Depends(get_db),
):
    """Get configuration request details."""
    use_case = ListAllConfigurationRequestsUseCase(db)

    result = await use_case.get_by_id(request_id=request_id)

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration request not found",
        )

    return result


@router.patch(
    "/requests/{request_id}",
    response_model=ConfigurationRequestResponse,
    summary="Update configuration request",
    description="Update a configuration request (assign, change status, add notes).",
)
async def update_request(
    request_id: UUID,
    update: ConfigurationRequestUpdate,
    admin=Depends(require_admin),
    db=Depends(get_db),
):
    """Update a configuration request."""
    use_case = UpdateConfigurationRequestUseCase(db)

    try:
        result = await use_case.execute(
            request_id=request_id,
            admin_id=admin.id,
            status=update.status,
            priority=update.priority,
            admin_notes=update.admin_notes,
            assigned_to=update.assigned_to,
        )
        return result
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration request not found",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/configure",
    response_model=CredentialStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Configure credentials",
    description="""
    Configure credentials for a merchant's service.

    This is the secure endpoint where actual API keys are entered.
    Credentials are validated with the provider before being encrypted
    and stored.

    **Security**: Credentials are encrypted using AES-256 before storage.
    """,
)
async def configure_credentials(
    config: CredentialConfigureRequest,
    admin=Depends(require_admin),
    db=Depends(get_db),
):
    """Configure credentials for a merchant."""
    use_case = ConfigureCredentialsUseCase(db)

    try:
        result = await use_case.execute(
            tenant_id=config.tenant_id,
            admin_id=admin.id,
            service_type=config.service_type,
            service_name=config.service_name,
            credentials=config.credentials,
            request_id=config.request_id,
            admin_notes=config.admin_notes,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/validate",
    response_model=CredentialValidationResponse,
    summary="Validate credentials",
    description="""
    Validate credentials without storing them.

    Use this to test credentials before configuring them for a merchant.
    """,
)
async def validate_credentials(
    request: CredentialValidateRequest,
    admin=Depends(require_admin),
    db=Depends(get_db),
):
    """Validate credentials without storing."""
    use_case = ValidateCredentialsUseCase(db)

    result = await use_case.execute(
        service_type=request.service_type,
        service_name=request.service_name,
        credentials=request.credentials,
    )

    return CredentialValidationResponse(
        is_valid=result.is_valid,
        status=result.status.value,
        message=result.message,
        details=result.details,
        error_code=result.error_code,
    )


@router.get(
    "/status/{tenant_id}/{service_type}/{service_name}",
    response_model=CredentialStatusResponse,
    summary="Get credential status",
    description="Get the credential status for a specific merchant and service.",
)
async def get_credential_status(
    tenant_id: UUID,
    service_type: ServiceType,
    service_name: ServiceName,
    admin=Depends(require_admin),
    db=Depends(get_db),
):
    """Get credential status for a merchant's service."""
    use_case = ConfigureCredentialsUseCase(db)

    result = await use_case.get_status(
        tenant_id=tenant_id,
        service_type=service_type,
        service_name=service_name,
    )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credentials not configured for this service",
        )

    return result


@router.delete(
    "/{tenant_id}/{service_type}/{service_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke credentials",
    description="""
    Revoke/delete credentials for a merchant's service.

    **Warning**: This action cannot be undone. The merchant will need
    to request new credentials to be configured.

    **Requires**: Super admin privileges.
    """,
)
async def revoke_credentials(
    tenant_id: UUID,
    service_type: ServiceType,
    service_name: ServiceName,
    admin=Depends(require_super_admin),
    db=Depends(get_db),
):
    """Revoke credentials for a merchant's service."""
    use_case = RevokeCredentialsUseCase(db)

    try:
        await use_case.execute(
            tenant_id=tenant_id,
            admin_id=admin.id,
            service_type=service_type,
            service_name=service_name,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credentials not found"
        )


@router.get(
    "/supported-services",
    response_model=SupportedServicesResponse,
    summary="Get supported services",
    description="Get list of all supported services and their required credentials.",
)
async def get_supported_services(
    admin=Depends(require_admin),
):
    """Get list of supported services."""
    use_case = GetSupportedServicesUseCase()
    return await use_case.execute()


@router.get(
    "/service-info/{service_type}/{service_name}",
    response_model=ServiceInfoResponse,
    summary="Get service information",
    description="Get detailed information about a specific service.",
)
async def get_service_info(
    service_type: ServiceType,
    service_name: ServiceName,
    admin=Depends(require_admin),
):
    """Get information about a specific service."""
    use_case = GetSupportedServicesUseCase()

    result = await use_case.get_service_info(
        service_type=service_type,
        service_name=service_name,
    )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service not found"
        )

    return result
