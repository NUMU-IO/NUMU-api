"""Email templates for NUMU transactional emails."""

from src.infrastructure.external_services.resend.email_templates.notifications import (
    DELIVERY_CONFIRMATION_TEMPLATE,
    ORDER_CONFIRMATION_TEMPLATE,
    SHIPPING_NOTIFICATION_TEMPLATE,
)
from src.infrastructure.external_services.resend.email_templates.onboarding import (
    FIRST_ORDER_RECEIVED_TEMPLATE,
    FIRST_PRODUCT_ADDED_TEMPLATE,
    WELCOME_TEMPLATE,
)

__all__ = [
    "WELCOME_TEMPLATE",
    "FIRST_PRODUCT_ADDED_TEMPLATE",
    "FIRST_ORDER_RECEIVED_TEMPLATE",
    "ORDER_CONFIRMATION_TEMPLATE",
    "SHIPPING_NOTIFICATION_TEMPLATE",
    "DELIVERY_CONFIRMATION_TEMPLATE",
]
