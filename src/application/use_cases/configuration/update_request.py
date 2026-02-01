"""Use case for updating configuration requests (admin)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.configuration import (
    ConfigurationRequest,
    CredentialAuditLog,
    RequestStatus,
    RequestPriority,
    AuditAction,
)


class UpdateConfigurationRequestUseCase:
    """Use case for updating a configuration request (admin)."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def execute(
        self,
        request_id: UUID,
        admin_id: UUID,
        status: Optional[RequestStatus] = None,
        priority: Optional[RequestPriority] = None,
        admin_notes: Optional[str] = None,
        assigned_to: Optional[UUID] = None,
    ) -> ConfigurationRequest:
        """Update a configuration request.
        
        Args:
            request_id: The request ID to update
            admin_id: The admin making the update
            status: New status (optional)
            priority: New priority (optional)
            admin_notes: Admin notes (optional)
            assigned_to: Admin ID to assign to (optional)
        
        Returns:
            Updated ConfigurationRequest
        
        Raises:
            LookupError: If request not found
            ValueError: If update is invalid
        """
        # Get the request
        result = await self.db.execute(
            select(ConfigurationRequest)
            .where(ConfigurationRequest.id == request_id)
        )
        request = result.scalar_one_or_none()
        
        if not request:
            raise LookupError("Configuration request not found")
        
        # Track changes for audit
        changes = {}
        
        # Update fields
        if status is not None:
            # Validate status transition
            self._validate_status_transition(request.status, status)
            changes["status"] = {"from": request.status.value, "to": status.value}
            request.status = status
            
            # Set completed_at if completing
            if status == RequestStatus.COMPLETED:
                request.completed_at = datetime.utcnow()
        
        if priority is not None:
            changes["priority"] = {"from": request.priority.value, "to": priority.value}
            request.priority = priority
        
        if admin_notes is not None:
            changes["admin_notes"] = {"updated": True}
            request.admin_notes = admin_notes
        
        if assigned_to is not None:
            changes["assigned_to"] = {"from": str(request.assigned_to), "to": str(assigned_to)}
            request.assigned_to = assigned_to
        
        # Create audit log
        audit_log = CredentialAuditLog(
            tenant_id=request.tenant_id,
            user_id=admin_id,
            action=AuditAction.REQUEST_UPDATED,
            service_type=request.service_type,
            service_name=request.service_name,
            details={
                "request_id": str(request_id),
                "changes": changes,
            }
        )
        self.db.add(audit_log)
        
        await self.db.commit()
        await self.db.refresh(request)
        
        return request
    
    def _validate_status_transition(
        self,
        current: RequestStatus,
        new: RequestStatus
    ) -> None:
        """Validate that a status transition is allowed.
        
        Args:
            current: Current status
            new: New status
        
        Raises:
            ValueError: If transition is not allowed
        """
        # Define allowed transitions
        allowed_transitions = {
            RequestStatus.PENDING: [
                RequestStatus.IN_PROGRESS,
                RequestStatus.CANCELLED,
                RequestStatus.REJECTED,
            ],
            RequestStatus.IN_PROGRESS: [
                RequestStatus.COMPLETED,
                RequestStatus.FAILED,
                RequestStatus.CANCELLED,
            ],
            RequestStatus.FAILED: [
                RequestStatus.IN_PROGRESS,  # Retry
                RequestStatus.CANCELLED,
            ],
            # Terminal states - no transitions allowed
            RequestStatus.COMPLETED: [],
            RequestStatus.CANCELLED: [],
            RequestStatus.REJECTED: [],
        }
        
        if new not in allowed_transitions.get(current, []):
            raise ValueError(
                f"Cannot transition from '{current.value}' to '{new.value}'"
            )
