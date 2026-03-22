"""Factory for creating gateway validators.

This module provides a factory class that creates the appropriate validator
based on service type and name.
"""

from src.infrastructure.database.models.tenant.configuration import (
    ServiceName,
    ServiceType,
)

from .base import GatewayValidator, GatewayValidatorError
from .communication_validators import (
    TwilioValidator,
    WhatsAppValidator,
)
from .payment_validators import (
    FawryValidator,
    KashierValidator,
    PaymobValidator,
    StripeValidator,
    TapValidator,
    VodafoneCashValidator,
)
from .shipping_validators import (
    AramexValidator,
    BostaValidator,
    MylerzValidator,
)


class ValidatorNotFoundError(GatewayValidatorError):
    """Raised when no validator is found for the given service."""

    pass


class GatewayValidatorFactory:
    """Factory for creating gateway validators.

    This factory maintains a registry of validators and creates the appropriate
    validator instance based on service type and name.

    Usage:
        factory = GatewayValidatorFactory()
        validator = factory.get_validator(
            service_type=ServiceType.PAYMENT_GATEWAY,
            service_name=ServiceName.FAWRY
        )
        result = await validator.validate(credentials)
    """

    # Registry of validators by service type and name
    _VALIDATORS: dict[ServiceType, dict[ServiceName, type[GatewayValidator]]] = {
        ServiceType.PAYMENT_GATEWAY: {
            ServiceName.FAWRY: FawryValidator,
            ServiceName.PAYMOB: PaymobValidator,
            ServiceName.VODAFONE_CASH: VodafoneCashValidator,
            ServiceName.STRIPE: StripeValidator,
            ServiceName.TAP: TapValidator,
            ServiceName.KASHIER: KashierValidator,
        },
        ServiceType.SHIPPING_CARRIER: {
            ServiceName.ARAMEX: AramexValidator,
            ServiceName.BOSTA: BostaValidator,
            ServiceName.MYLERZ: MylerzValidator,
        },
        ServiceType.WHATSAPP: {
            ServiceName.WHATSAPP_BUSINESS: WhatsAppValidator,
        },
        ServiceType.SMS: {
            ServiceName.TWILIO: TwilioValidator,
        },
    }

    def get_validator(
        self, service_type: ServiceType, service_name: ServiceName
    ) -> GatewayValidator:
        """Get a validator instance for the given service.

        Args:
            service_type: Type of service (payment, shipping, etc.)
            service_name: Specific service provider name

        Returns:
            GatewayValidator instance for the service.

        Raises:
            ValidatorNotFoundError: If no validator exists for the service.
        """
        type_validators = self._VALIDATORS.get(service_type)
        if not type_validators:
            raise ValidatorNotFoundError(
                f"No validators registered for service type: {service_type}"
            )

        validator_class = type_validators.get(service_name)
        if not validator_class:
            raise ValidatorNotFoundError(
                f"No validator registered for service: {service_type}/{service_name}"
            )

        return validator_class()

    def is_supported(
        self, service_type: ServiceType, service_name: ServiceName
    ) -> bool:
        """Check if a service is supported by the validator factory.

        Args:
            service_type: Type of service
            service_name: Specific service provider name

        Returns:
            True if a validator exists, False otherwise.
        """
        type_validators = self._VALIDATORS.get(service_type, {})
        return service_name in type_validators

    def get_supported_services(self) -> dict[ServiceType, list[ServiceName]]:
        """Get all supported services.

        Returns:
            Dictionary mapping service types to lists of supported service names.
        """
        return {
            service_type: list(validators.keys())
            for service_type, validators in self._VALIDATORS.items()
        }

    def get_required_fields(
        self, service_type: ServiceType, service_name: ServiceName
    ) -> list[str]:
        """Get required credential fields for a service.

        Args:
            service_type: Type of service
            service_name: Specific service provider name

        Returns:
            List of required field names.

        Raises:
            ValidatorNotFoundError: If no validator exists for the service.
        """
        validator = self.get_validator(service_type, service_name)
        return validator.required_fields

    def get_optional_fields(
        self, service_type: ServiceType, service_name: ServiceName
    ) -> list[str]:
        """Get optional credential fields for a service.

        Args:
            service_type: Type of service
            service_name: Specific service provider name

        Returns:
            List of optional field names.

        Raises:
            ValidatorNotFoundError: If no validator exists for the service.
        """
        validator = self.get_validator(service_type, service_name)
        return validator.optional_fields


# Singleton instance
_factory: GatewayValidatorFactory | None = None


def get_validator_factory() -> GatewayValidatorFactory:
    """Get or create the singleton factory instance.

    Returns:
        GatewayValidatorFactory instance.
    """
    global _factory
    if _factory is None:
        _factory = GatewayValidatorFactory()
    return _factory
