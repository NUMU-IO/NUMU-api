"""Service dependencies."""

from src.infrastructure.external_services import (
    password_service,
    token_service,
)
from src.infrastructure.external_services.cloudflare_r2 import (
    CloudflareR2StorageService,
)
from src.infrastructure.external_services.image import ImagePipeline, ImageProcessor
from src.infrastructure.external_services.openai import OpenAIService
from src.infrastructure.external_services.resend import ResendEmailService
from src.infrastructure.external_services.stripe import StripePaymentService
from src.infrastructure.external_services.totp_service import totp_service


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


def get_storage_service():
    """Get storage service dependency."""
    return CloudflareR2StorageService()


def get_ai_service():
    """Get AI service dependency."""
    return OpenAIService()


def get_image_pipeline():
    """Get image processing pipeline dependency."""
    return ImagePipeline(
        image_processor=ImageProcessor(),
        storage_service=CloudflareR2StorageService(),
    )
