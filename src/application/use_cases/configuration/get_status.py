"""Use case for getting configuration status."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.schemas.tenant.configuration import ConfigurationStatusResponse
from src.infrastructure.database.models.tenant.configuration import (
    ConfigurationRequest,
    RequestStatus,
    ServiceCredential,
    ServiceName,
    ServiceType,
)
from src.infrastructure.external_services.gateway_validators import (
    get_validator_factory,
)


class GetConfigurationStatusUseCase:
    """Use case for getting configuration status of a service.

    Returns comprehensive status including:
    - Whether credentials are configured
    - Whether they're validated
    - Any pending requests
    - Safe display information
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.validator_factory = get_validator_factory()

    async def execute(
        self,
        tenant_id: UUID,
        service_type: ServiceType,
        service_name: ServiceName,
    ) -> ConfigurationStatusResponse:
        """Get configuration status for a service.

        Args:
            tenant_id: The tenant/merchant ID
            service_type: Type of service
            service_name: Specific service provider

        Returns:
            ConfigurationStatusResponse with complete status
        """
        # Get credentials if configured
        creds_result = await self.db.execute(
            select(ServiceCredential)
            .where(ServiceCredential.tenant_id == tenant_id)
            .where(ServiceCredential.service_type == service_type)
            .where(ServiceCredential.service_name == service_name)
        )
        credentials = creds_result.scalar_one_or_none()

        # Get pending request if any
        request_result = await self.db.execute(
            select(ConfigurationRequest)
            .where(ConfigurationRequest.tenant_id == tenant_id)
            .where(ConfigurationRequest.service_type == service_type)
            .where(ConfigurationRequest.service_name == service_name)
            .where(ConfigurationRequest.status.in_([
                RequestStatus.PENDING,
                RequestStatus.IN_PROGRESS
            ]))
        )
        pending_request = request_result.scalar_one_or_none()

        # Build display info from metadata if available
        display_info = None
        if credentials and credentials.extra_metadata:
            display_info = credentials.extra_metadata.get("display_info")

        return ConfigurationStatusResponse(
            service_type=service_type,
            service_name=service_name,
            is_configured=credentials is not None,
            is_active=credentials.is_active if credentials else False,
            is_validated=credentials.is_validated if credentials else False,
            last_configured_at=credentials.updated_at if credentials else None,
            last_validated_at=credentials.last_validated_at if credentials else None,
            has_pending_request=pending_request is not None,
            pending_request_id=pending_request.id if pending_request else None,
            pending_request_status=pending_request.status if pending_request else None,
            display_info=display_info,
        )
