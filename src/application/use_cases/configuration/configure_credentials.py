"""Use case for configuring credentials (admin)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.schemas.tenant.configuration.credential_schemas import (
    CredentialStatusResponse,
)
from src.infrastructure.database.models.tenant.configuration import (
    AuditAction,
    ConfigurationRequest,
    CredentialAuditLog,
    RequestStatus,
    ServiceCredential,
    ServiceName,
    ServiceType,
)
from src.infrastructure.external_services.gateway_validators import (
    get_validator_factory,
)
from src.infrastructure.external_services.secrets import (
    get_secrets_manager,
)


class ConfigureCredentialsUseCase:
    """Use case for configuring service credentials (admin only).

    This use case handles:
    1. Validating credentials with the provider
    2. Encrypting credentials
    3. Storing encrypted credentials
    4. Updating related configuration request
    5. Creating audit log
    6. Notifying merchant
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.validator_factory = get_validator_factory()
        self.secrets_manager = get_secrets_manager()

    async def execute(
        self,
        tenant_id: UUID,
        admin_id: UUID,
        service_type: ServiceType,
        service_name: ServiceName,
        credentials: dict[str, Any],
        request_id: UUID | None = None,
        admin_notes: str | None = None,
    ) -> CredentialStatusResponse:
        """Configure credentials for a merchant's service.

        Args:
            tenant_id: The tenant/merchant ID
            admin_id: The admin configuring credentials
            service_type: Type of service
            service_name: Specific service provider
            credentials: The credentials to configure
            request_id: Optional configuration request ID
            admin_notes: Optional notes from admin

        Returns:
            CredentialStatusResponse with configuration status

        Raises:
            ValueError: If credentials are invalid
        """
        # Step 1: Validate credentials with provider
        validator = self.validator_factory.get_validator(service_type, service_name)
        validation_result = await validator.validate(credentials)

        if not validation_result.is_valid:
            raise ValueError(
                f"Credential validation failed: {validation_result.message}"
            )

        # Step 2: Get display info (masked credentials)
        display_info = validator.get_display_info(credentials)

        # Step 3: Encrypt credentials
        credentials_encrypted = self.secrets_manager.encrypt_credentials(credentials)

        # Step 4: Check for existing credentials
        existing_result = await self.db.execute(
            select(ServiceCredential)
            .where(ServiceCredential.tenant_id == tenant_id)
            .where(ServiceCredential.service_type == service_type)
            .where(ServiceCredential.service_name == service_name)
        )
        existing = existing_result.scalar_one_or_none()

        now = datetime.utcnow()

        if existing:
            # Update existing credentials
            existing.credentials_encrypted = credentials_encrypted
            existing.is_validated = True
            existing.is_active = True
            existing.last_validated_at = now
            existing.configured_by = admin_id
            existing.extra_metadata = {
                "display_info": display_info,
                "validation_details": validation_result.details,
            }
            credential = existing
        else:
            # Create new credentials
            credential = ServiceCredential(
                tenant_id=tenant_id,
                service_type=service_type,
                service_name=service_name,
                credentials_encrypted=credentials_encrypted,
                is_validated=True,
                is_active=True,
                last_validated_at=now,
                configured_by=admin_id,
                extra_metadata={
                    "display_info": display_info,
                    "validation_details": validation_result.details,
                },
            )
            self.db.add(credential)

        # Step 5: Update configuration request if provided
        if request_id:
            request_result = await self.db.execute(
                select(ConfigurationRequest).where(
                    ConfigurationRequest.id == request_id
                )
            )
            request = request_result.scalar_one_or_none()

            if request:
                request.status = RequestStatus.COMPLETED
                request.completed_at = now
                request.admin_notes = admin_notes

        # Step 6: Create audit log
        audit_log = CredentialAuditLog(
            tenant_id=tenant_id,
            user_id=admin_id,
            action=AuditAction.CREDENTIALS_CONFIGURED,
            service_type=service_type,
            service_name=service_name,
            details={
                "request_id": str(request_id) if request_id else None,
                "validation_status": validation_result.status.value,
                "is_update": existing is not None,
            },
        )
        self.db.add(audit_log)

        await self.db.commit()
        await self.db.refresh(credential)

        # Auto-complete onboarding step based on service type
        await self._try_onboarding_step(tenant_id, service_type)

        return CredentialStatusResponse(
            tenant_id=tenant_id,
            service_type=service_type,
            service_name=service_name,
            is_configured=True,
            is_active=True,
            is_validated=True,
            configured_at=credential.updated_at,
            configured_by=admin_id,
            last_validated_at=now,
            display_info=display_info,
        )

    async def _try_onboarding_step(
        self, tenant_id: UUID, service_type: ServiceType
    ) -> None:
        """Best-effort onboarding step completion after credential config."""
        try:
            from src.application.use_cases.onboarding.auto_complete import (
                try_complete_onboarding_step,
            )
            from src.core.entities.onboarding import OnboardingStepKey
            from src.infrastructure.database.models.tenant.store import StoreModel
            from src.infrastructure.repositories.onboarding_repository import (
                OnboardingRepository,
            )

            step_map = {
                ServiceType.PAYMENT_GATEWAY: OnboardingStepKey.CONFIGURE_PAYMENT,
                ServiceType.SHIPPING_CARRIER: OnboardingStepKey.ADD_SHIPPING,
            }
            step_key = step_map.get(service_type)
            if not step_key:
                return

            # Find the store belonging to this tenant
            result = await self.db.execute(
                select(StoreModel.id).where(StoreModel.tenant_id == tenant_id).limit(1)
            )
            store_id = result.scalar_one_or_none()
            if not store_id:
                return

            onboarding_repo = OnboardingRepository(self.db)
            await try_complete_onboarding_step(onboarding_repo, store_id, step_key)
        except Exception:
            pass  # Never block credential configuration for onboarding

    async def get_status(
        self,
        tenant_id: UUID,
        service_type: ServiceType,
        service_name: ServiceName,
    ) -> CredentialStatusResponse | None:
        """Get credential status for a service.

        Args:
            tenant_id: The tenant/merchant ID
            service_type: Type of service
            service_name: Specific service provider

        Returns:
            CredentialStatusResponse if configured, None otherwise
        """
        result = await self.db.execute(
            select(ServiceCredential)
            .where(ServiceCredential.tenant_id == tenant_id)
            .where(ServiceCredential.service_type == service_type)
            .where(ServiceCredential.service_name == service_name)
        )
        credential = result.scalar_one_or_none()

        if not credential:
            return None

        display_info = None
        if credential.extra_metadata:
            display_info = credential.extra_metadata.get("display_info")

        return CredentialStatusResponse(
            tenant_id=tenant_id,
            service_type=service_type,
            service_name=service_name,
            is_configured=True,
            is_active=credential.is_active,
            is_validated=credential.is_validated,
            configured_at=credential.updated_at,
            configured_by=credential.configured_by,
            last_validated_at=credential.last_validated_at,
            display_info=display_info,
        )
