"""Service dependencies."""

import logging
from typing import Annotated

from fastapi import Depends

from src.api.dependencies.repositories import (
    get_email_log_repository,
    get_email_template_repository,
)
from src.application.services.email_template_renderer import EmailTemplateRenderer
from src.config import settings
from src.core.interfaces.repositories.email_log_repository import (
    IEmailLogRepository,
)
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
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


def get_email_template_renderer(
    repo: Annotated[IEmailTemplateRepository, Depends(get_email_template_repository)],
) -> EmailTemplateRenderer:
    """Get email-template renderer dependency.

    The renderer reads merchant overrides through the repository and
    falls back to the registry default when no override exists.
    """
    return EmailTemplateRenderer(email_template_repo=repo)


def get_email_service(
    renderer: Annotated[
        EmailTemplateRenderer | None, Depends(get_email_template_renderer)
    ] = None,
    email_log_repo: Annotated[
        IEmailLogRepository | None, Depends(get_email_log_repository)
    ] = None,
):
    """Get email service dependency.

    Wires the merchant-facing ``EmailTemplateRenderer`` and the email
    audit log repository into the service. Callers that pass a
    ``store_id`` to the service's per-event methods will get merchant
    custom-template behavior and an audit row written; callers that
    don't get the legacy behavior unchanged.

    Defaults of ``None`` allow non-DI callers (Celery workers, ad-hoc
    helpers) to invoke ``get_email_service()`` directly with no
    arguments — they get a service in legacy mode.
    """
    return ResendEmailService(renderer=renderer, email_log_repo=email_log_repo)


def get_payment_service():
    """Get payment service dependency."""
    return StripePaymentService()


def get_payment_service_for_provider(provider: str):
    """Get payment service for a specific provider.

    Used by the refund system to resolve the correct payment
    service based on the order's original payment method.

    For Paymob, use get_paymob_service_for_store() instead
    to get a store-specific instance with merchant credentials.
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
        case "kashier":
            from src.infrastructure.external_services.kashier import (
                KashierPaymentService,
            )

            return KashierPaymentService()
        case "fawaterak":
            from src.infrastructure.external_services.fawaterak import (
                FawaterakPaymentService,
            )

            return FawaterakPaymentService()
        case _:
            raise ValueError(f"Unknown payment provider: {provider}")


async def get_paymob_service_for_store(store_settings: dict):
    """Get a PaymobPaymentService configured with a store's credentials.

    Args:
        store_settings: The store.settings dict containing encrypted credentials.

    Returns:
        PaymobPaymentService configured with the merchant's own keys.
    """
    from src.infrastructure.external_services.paymob.payment_service import (
        PaymobPaymentService,
        get_merchant_paymob_credentials,
    )

    creds = await get_merchant_paymob_credentials(store_settings)
    return PaymobPaymentService(
        secret_key=creds["secret_key"],
        public_key=creds["public_key"],
        hmac_secret=creds["hmac_secret"],
        card_integration_id=creds.get("card_integration_id"),
        wallet_integration_id=creds.get("wallet_integration_id"),
    )


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


def get_proof_vision_service_for_store(store_settings: dict):
    """Pick a vision OCR provider for an InstaPay proof submission.

    The provider is admin-assigned per-store via
    ``store.settings.payment.instapay.ocr_provider``. Merchants
    cannot self-select — the merchant credentials PUT silently drops
    that field, see :func:`save_instapay_credentials`. Falls back to
    a Noop provider (status="skipped") whenever:

      * the store has no provider assigned, or
      * the assigned provider is unknown / typo, or
      * the assigned provider is ``google_vision`` but the API key
        env var is unset.

    Soft-fail by design: the auto-approval engine treats every non-OK
    result as "no signal", so a missing config never breaks checkout.
    """
    from src.infrastructure.external_services.vision import (
        DeepSeekHFProofService,
        GlmHFProofService,
        GoogleVisionProofService,
        IProofVisionService,
        NoopProofVisionService,
    )

    instapay_settings = (store_settings or {}).get("payment", {}).get("instapay", {})
    provider = (instapay_settings.get("ocr_provider") or "").strip().lower()

    impl: IProofVisionService
    if provider == "google_vision":
        if not settings.google_vision_api_key:
            _logger.warning(
                "ocr_provider=google_vision but no GOOGLE_VISION_API_KEY set; "
                "falling back to noop"
            )
            impl = NoopProofVisionService()
        else:
            impl = GoogleVisionProofService(
                api_key=settings.google_vision_api_key,
            )
    elif provider == "deepseek_hf":
        impl = DeepSeekHFProofService(hf_token=settings.huggingface_token)
    elif provider == "glm_hf":
        impl = GlmHFProofService(hf_token=settings.huggingface_token)
    else:
        impl = NoopProofVisionService()

    return impl


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
