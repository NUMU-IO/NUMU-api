"""Gateway validators for external service credential validation.

This module provides validators for verifying credentials with external
service providers before storing them. This ensures that only valid
credentials are saved, preventing configuration errors.

Supported Services:
- Payment Gateways: Fawry, Fawaterak, Paymob, Vodafone Cash, Stripe, Tap
- Shipping Carriers: Aramex, Bosta, MylerZ
- Communication: WhatsApp Business API, Twilio
"""

from .base import GatewayValidator, GatewayValidatorError, ValidationResult
from .communication_validators import (
    TwilioValidator,
    WhatsAppValidator,
)
from .payment_validators import (
    FawaterakValidator,
    FawryValidator,
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
from .validator_factory import GatewayValidatorFactory, get_validator_factory

__all__ = [
    # Base
    "GatewayValidator",
    "ValidationResult",
    "GatewayValidatorError",
    # Factory
    "GatewayValidatorFactory",
    "get_validator_factory",
    # Payment
    "FawaterakValidator",
    "FawryValidator",
    "PaymobValidator",
    "VodafoneCashValidator",
    "StripeValidator",
    "TapValidator",
    # Shipping
    "AramexValidator",
    "BostaValidator",
    "MylerzValidator",
    # Communication
    "WhatsAppValidator",
    "TwilioValidator",
]
