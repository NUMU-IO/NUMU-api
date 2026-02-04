"""Use case for validating credentials without storing."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.configuration import (
    ServiceName,
    ServiceType,
)
from src.infrastructure.external_services.gateway_validators import (
    ValidationResult,
    get_validator_factory,
)


class ValidateCredentialsUseCase:
    """Use case for validating credentials without storing them.

    This allows admins to test credentials before configuring them
    for a merchant.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.validator_factory = get_validator_factory()

    async def execute(
        self,
        service_type: ServiceType,
        service_name: ServiceName,
        credentials: dict[str, Any],
    ) -> ValidationResult:
        """Validate credentials with the provider.

        Args:
            service_type: Type of service
            service_name: Specific service provider
            credentials: Credentials to validate

        Returns:
            ValidationResult with validation outcome
        """
        validator = self.validator_factory.get_validator(service_type, service_name)
        return await validator.validate(credentials)
