"""Use case for creating configuration requests."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.configuration import (
    AuditAction,
    ConfigurationRequest,
    CredentialAuditLog,
    RequestPriority,
    RequestStatus,
    ServiceCredential,
    ServiceName,
    ServiceType,
)


class CreateConfigurationRequestUseCase:
    """Use case for creating a new configuration request.

    This use case handles:
    1. Checking if credentials are already configured
    2. Checking for existing pending requests
    3. Creating the new request
    4. Logging the action for audit
    5. Triggering notifications (via event)
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def execute(
        self,
        tenant_id: UUID,
        user_id: UUID,
        service_type: ServiceType,
        service_name: ServiceName,
        notes: str | None = None,
        priority: RequestPriority = RequestPriority.NORMAL,
    ) -> ConfigurationRequest:
        """Create a new configuration request.

        Args:
            tenant_id: The tenant/merchant ID
            user_id: The user creating the request
            service_type: Type of service to configure
            service_name: Specific service provider
            notes: Optional notes from the merchant
            priority: Request priority level

        Returns:
            The created ConfigurationRequest

        Raises:
            ValueError: If credentials are already configured or request exists
        """
        # Check if credentials are already configured
        existing_creds = await self.db.execute(
            select(ServiceCredential)
            .where(ServiceCredential.tenant_id == tenant_id)
            .where(ServiceCredential.service_type == service_type)
            .where(ServiceCredential.service_name == service_name)
            .where(ServiceCredential.is_active)
        )
        if existing_creds.scalar_one_or_none():
            raise ValueError(
                f"Credentials for {service_name} are already configured. "
                "Contact support if you need to update them."
            )

        # Check for existing pending request
        existing_request = await self.db.execute(
            select(ConfigurationRequest)
            .where(ConfigurationRequest.tenant_id == tenant_id)
            .where(ConfigurationRequest.service_type == service_type)
            .where(ConfigurationRequest.service_name == service_name)
            .where(ConfigurationRequest.status.in_([
                RequestStatus.PENDING,
                RequestStatus.IN_PROGRESS
            ]))
        )
        if existing_request.scalar_one_or_none():
            raise ValueError(
                f"A configuration request for {service_name} is already pending. "
                "Please wait for it to be processed."
            )

        # Create the request
        request = ConfigurationRequest(
            tenant_id=tenant_id,
            requested_by=user_id,
            service_type=service_type,
            service_name=service_name,
            merchant_notes=notes,
            priority=priority,
            status=RequestStatus.PENDING,
        )

        self.db.add(request)

        # Create audit log
        audit_log = CredentialAuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=AuditAction.REQUEST_CREATED,
            service_type=service_type,
            service_name=service_name,
            details={
                "request_id": str(request.id),
                "priority": priority.value,
                "has_notes": bool(notes),
            }
        )
        self.db.add(audit_log)

        await self.db.commit()
        await self.db.refresh(request)

        # TODO: Emit event for notification service
        # await self.event_bus.publish(ConfigurationRequestCreated(request))

        return request
