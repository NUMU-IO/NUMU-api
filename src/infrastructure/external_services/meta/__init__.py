"""Meta (Facebook/Instagram) external services."""

from src.infrastructure.external_services.meta.catalog_client import CatalogClient
from src.infrastructure.external_services.meta.conversions_api import (
    CapiClient,
    hash_email,
    hash_name,
    hash_phone,
)
from src.infrastructure.external_services.meta.graph_client import (
    MetaAuthenticationError,
    MetaGraphAPIError,
    MetaGraphClient,
    MetaRateLimitError,
)
from src.infrastructure.external_services.meta.instagram_client import InstagramClient
from src.infrastructure.external_services.meta.messenger_client import MessengerClient
from src.infrastructure.external_services.meta.oauth_service import MetaOAuthService
from src.infrastructure.external_services.meta.rate_limiter import (
    MetaRateLimiter,
    RateLimiterRegistry,
)
from src.infrastructure.external_services.meta.schemas import (
    MetaConversationRequest,
    MetaMessageRequest,
    MetaWebhookPayload,
    WhatsAppStatusPayload,
    WhatsAppWebhookPayload,
)
from src.infrastructure.external_services.meta.signature import (
    verify_meta_webhook,
    verify_whatsapp_webhook,
    verify_x_hub_signature,
)
from src.infrastructure.external_services.meta.social_service import MetaSocialService
from src.infrastructure.external_services.meta.template_client import TemplateClient
from src.infrastructure.external_services.meta.whatsapp_client import WhatsAppClient

__all__ = [
    # Clients
    "CatalogClient",
    "CapiClient",
    "MetaGraphClient",
    "InstagramClient",
    "MessengerClient",
    "MetaOAuthService",
    "MetaRateLimiter",
    "RateLimiterRegistry",
    "TemplateClient",
    "WhatsAppClient",
    # Exceptions
    "MetaGraphAPIError",
    "MetaAuthenticationError",
    "MetaRateLimitError",
    # Helpers
    "hash_email",
    "hash_name",
    "hash_phone",
    # Signature
    "verify_x_hub_signature",
    "verify_meta_webhook",
    "verify_whatsapp_webhook",
    # Schemas
    "MetaMessageRequest",
    "MetaConversationRequest",
    "MetaWebhookPayload",
    "WhatsAppWebhookPayload",
    "WhatsAppStatusPayload",
    # Legacy
    "MetaSocialService",
]
