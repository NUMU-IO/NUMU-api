"""Service dependencies."""

import logging

from src.config import settings
from src.infrastructure.cache import ProductCacheService, get_product_cache
from src.infrastructure.external_services import (
    password_service,
    token_service,
)
from src.infrastructure.external_services.cloudflare_r2 import (
    CloudflareR2StorageService,
)
from src.infrastructure.external_services.image import ImagePipeline, ImageProcessor
from src.infrastructure.external_services.local_storage import LocalStorageService
from src.infrastructure.external_services.openai import OpenAIService
from src.infrastructure.external_services.resend import ResendEmailService
from src.infrastructure.external_services.stripe import StripePaymentService
from src.infrastructure.external_services.totp_service import totp_service

_logger = logging.getLogger(__name__)


def get_password_service():
    """Get password service dependency."""
    return password_service


def get_token_service():
    """Get token service dependency."""
    return token_service


def get_totp_service():
    """Get TOTP service dependency for 2FA."""
    return totp_service


def get_email_service():
    """Get email service dependency."""
    return ResendEmailService()


def get_payment_service():
    """Get payment service dependency."""
    return StripePaymentService()


def get_payment_service_for_provider(provider: str):
    """Get payment service for a specific provider.

    Used by the refund system to resolve the correct payment
    service based on the order's original payment method.
    """

    match provider:
        case "paymob":
            from src.infrastructure.external_services.paymob import (
                PaymobPaymentService,
            )

            return PaymobPaymentService()
        case "fawry":
            from src.infrastructure.external_services.fawry import (
                FawryPaymentService,
            )

            return FawryPaymentService()
        case "stripe":
            return StripePaymentService()
        case "cod":
            from src.infrastructure.external_services.cod import CODPaymentService

            return CODPaymentService()
        case _:
            raise ValueError(f"Unknown payment provider: {provider}")


def _get_storage():
    """Return S3 storage if configured, otherwise local filesystem storage."""
    # Prefer new s3_* settings, fall back to legacy r2_* settings
    has_s3 = (
        settings.s3_endpoint_url
        and settings.s3_access_key_id
        and settings.s3_secret_access_key
    )
    has_r2 = (
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
    )
    if has_s3 or has_r2:
        return CloudflareR2StorageService()
    _logger.info("S3 storage not configured — using local filesystem storage")
    return LocalStorageService()


def get_storage_service():
    """Get storage service dependency."""
    return _get_storage()


def get_ai_service():
    """Get AI service dependency."""
    return OpenAIService()


def get_image_pipeline():
    """Get image processing pipeline dependency."""
    return ImagePipeline(
        image_processor=ImageProcessor(),
        storage_service=_get_storage(),
    )


def get_product_cache_service() -> ProductCacheService:
    """Get product cache service dependency.

    Returns a singleton instance of ProductCacheService for
    caching product listings and category trees.
    """
    return get_product_cache()
