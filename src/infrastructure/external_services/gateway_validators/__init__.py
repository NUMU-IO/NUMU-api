"""Gateway validators for external service credential validation.

This module provides validators for verifying credentials with external
service providers before storing them. This ensures that only valid
credentials are saved, preventing configuration errors.

Supported Services:
- Payment Gateways: Fawry, Paymob, Vodafone Cash, Stripe, Tap
- Shipping Carriers: Aramex, Bosta, MylerZ
- Communication: WhatsApp Business API, Twilio
"""

from .base import GatewayValidator, ValidationResult, GatewayValidatorError
from .payment_validators import (
    FawryValidator,
    PaymobValidator,
    VodafoneCashValidator,
    StripeValidator,
    TapValidator,
)
from .shipping_validators import (
    AramexValidator,
    BostaValidator,
    MylerzValidator,
)
from .communication_validators import (
    WhatsAppValidator,
    TwilioValidator,
)
from .validator_factory import GatewayValidatorFactory

__all__ = [
    # Base
    "GatewayValidator",
    "ValidationResult",
    "GatewayValidatorError",
    # Factory
    "GatewayValidatorFactory",
    # Payment
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
