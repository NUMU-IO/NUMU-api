"""Use case for canceling configuration requests."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.configuration import (
    ConfigurationRequest,
    CredentialAuditLog,
    RequestStatus,
    AuditAction,
)


class CancelConfigurationRequestUseCase:
    """Use case for canceling a pending configuration request."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def execute(
        self,
        tenant_id: UUID,
        user_id: UUID,
        request_id: UUID,
    ) -> None:
        """Cancel a pending configuration request.
        
        Args:
            tenant_id: The tenant/merchant ID
            user_id: The user canceling the request
            request_id: The request ID to cancel
        
        Raises:
            LookupError: If request not found
            ValueError: If request cannot be canceled
        """
        # Get the request
        result = await self.db.execute(
            select(ConfigurationRequest)
            .where(ConfigurationRequest.id == request_id)
            .where(ConfigurationRequest.tenant_id == tenant_id)
        )
        request = result.scalar_one_or_none()
        
        if not request:
            raise LookupError("Configuration request not found")
        
        # Check if can be canceled
        if request.status not in [RequestStatus.PENDING]:
            raise ValueError(
                f"Cannot cancel request with status '{request.status.value}'. "
                "Only pending requests can be canceled."
            )
        
        # Update status
        request.status = RequestStatus.CANCELLED
        
        # Create audit log
        audit_log = CredentialAuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=AuditAction.REQUEST_CANCELLED,
            service_type=request.service_type,
            service_name=request.service_name,
            details={
                "request_id": str(request_id),
                "cancelled_by": "merchant",
            }
        )
        self.db.add(audit_log)
        
        await self.db.commit()
