"""Use case for revoking credentials (super admin)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.configuration import (
    ServiceCredential,
    CredentialAuditLog,
    ServiceType,
    ServiceName,
    AuditAction,
)


class RevokeCredentialsUseCase:
    """Use case for revoking/deleting credentials (super admin only).
    
    This permanently removes credentials for a service. The merchant
    will need to request new credentials to be configured.
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def execute(
        self,
        tenant_id: UUID,
        admin_id: UUID,
        service_type: ServiceType,
        service_name: ServiceName,
    ) -> None:
        """Revoke credentials for a merchant's service.
        
        Args:
            tenant_id: The tenant/merchant ID
            admin_id: The admin revoking credentials
            service_type: Type of service
            service_name: Specific service provider
        
        Raises:
            LookupError: If credentials not found
        """
        # Get existing credentials
        result = await self.db.execute(
            select(ServiceCredential)
            .where(ServiceCredential.tenant_id == tenant_id)
            .where(ServiceCredential.service_type == service_type)
            .where(ServiceCredential.service_name == service_name)
        )
        credential = result.scalar_one_or_none()
        
        if not credential:
            raise LookupError("Credentials not found")
        
        # Create audit log before deletion
        audit_log = CredentialAuditLog(
            tenant_id=tenant_id,
            user_id=admin_id,
            action=AuditAction.CREDENTIALS_REVOKED,
            service_type=service_type,
            service_name=service_name,
            details={
                "credential_id": str(credential.id),
                "was_active": credential.is_active,
            }
        )
        self.db.add(audit_log)
        
        # Delete credentials
        await self.db.delete(credential)
        await self.db.commit()
        
        # TODO: Emit event for notification service
        # await self.event_bus.publish(CredentialsRevoked(tenant_id, service_type, service_name))
