"""Use cases for listing configuration requests."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.schemas.tenant.configuration import (
    ConfigurationRequestListResponse,
    ConfigurationRequestResponse,
)
from src.infrastructure.database.models.tenant.configuration import (
    ConfigurationRequest,
    RequestStatus,
    ServiceType,
)


class ListConfigurationRequestsUseCase:
    """Use case for listing configuration requests for a specific tenant."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def execute(
        self,
        tenant_id: UUID,
        status_filter: RequestStatus | None = None,
        service_type: ServiceType | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> ConfigurationRequestListResponse:
        """List configuration requests for a tenant.

        Args:
            tenant_id: The tenant/merchant ID
            status_filter: Optional filter by status
            service_type: Optional filter by service type
            page: Page number (1-indexed)
            page_size: Number of items per page

        Returns:
            Paginated list of configuration requests
        """
        # Build base query
        query = select(ConfigurationRequest).where(
            ConfigurationRequest.tenant_id == tenant_id
        )

        # Apply filters
        if status_filter:
            query = query.where(ConfigurationRequest.status == status_filter)
        if service_type:
            query = query.where(ConfigurationRequest.service_type == service_type)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(ConfigurationRequest.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        # Execute query
        result = await self.db.execute(query)
        requests = result.scalars().all()

        return ConfigurationRequestListResponse(
            items=[ConfigurationRequestResponse.model_validate(r) for r in requests],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_by_id(
        self,
        tenant_id: UUID,
        request_id: UUID,
    ) -> ConfigurationRequest | None:
        """Get a specific request by ID for a tenant.

        Args:
            tenant_id: The tenant/merchant ID
            request_id: The request ID

        Returns:
            ConfigurationRequest if found, None otherwise
        """
        result = await self.db.execute(
            select(ConfigurationRequest)
            .where(ConfigurationRequest.id == request_id)
            .where(ConfigurationRequest.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()


class ListAllConfigurationRequestsUseCase:
    """Use case for listing all configuration requests (admin)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def execute(
        self,
        status_filter: RequestStatus | None = None,
        service_type: ServiceType | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> ConfigurationRequestListResponse:
        """List all configuration requests across tenants.

        Args:
            status_filter: Optional filter by status
            service_type: Optional filter by service type
            page: Page number (1-indexed)
            page_size: Number of items per page

        Returns:
            Paginated list of configuration requests
        """
        # Build base query
        query = select(ConfigurationRequest)

        # Apply filters
        if status_filter:
            query = query.where(ConfigurationRequest.status == status_filter)
        if service_type:
            query = query.where(ConfigurationRequest.service_type == service_type)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        # Apply pagination and ordering (prioritize by priority then date)
        query = query.order_by(
            ConfigurationRequest.priority.desc(),
            ConfigurationRequest.created_at.asc()
        )
        query = query.offset((page - 1) * page_size).limit(page_size)

        # Execute query
        result = await self.db.execute(query)
        requests = result.scalars().all()

        return ConfigurationRequestListResponse(
            items=[ConfigurationRequestResponse.model_validate(r) for r in requests],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_by_id(
        self,
        request_id: UUID,
    ) -> ConfigurationRequest | None:
        """Get a specific request by ID (admin).

        Args:
            request_id: The request ID

        Returns:
            ConfigurationRequest if found, None otherwise
        """
        result = await self.db.execute(
            select(ConfigurationRequest)
            .where(ConfigurationRequest.id == request_id)
        )
        return result.scalar_one_or_none()
