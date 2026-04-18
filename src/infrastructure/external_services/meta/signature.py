"""Meta webhook signature verification."""

import hashlib
import hmac

from src.config import settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)


def verify_x_hub_signature(
    payload: bytes | str,
    signature: str,
    app_secret: str | None = None,
) -> bool:
    """Verify X-Hub-Signature-256 header from Meta webhooks.

    Args:
        payload: Raw request body (bytes or str)
        signature: The X-Hub-Signature-256 header value
        app_secret: Meta app secret (defaults to settings)

    Returns:
        True if signature is valid, False otherwise
    """
    secret = app_secret or settings.meta_app_secret
    if not secret:
        logger.warning("meta_signature_verify_no_secret")
        return False

    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    expected_signature = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
    )

    if not hmac.compare_digest(signature, expected_signature):
        logger.warning(
            "meta_signature_invalid",
            expected_prefix=expected_signature[:20],
            received_prefix=signature[:20],
        )
        return False

    logger.debug("meta_signature_valid")
    return True


def verify_meta_webhook(
    mode: str,
    token: str,
    challenge: str,
) -> str | None:
    """Verify Meta webhook verification request (GET).

    When Meta sends a GET to verify the webhook, we need to
    echo back the challenge with the correct verify token.

    Args:
        mode: Webhook verification mode (usually "subscribe")
        token: The verify token we configured in Meta app
        challenge: The challenge string to echo back

    Returns:
        The challenge string if verification passes, None otherwise
    """
    expected_token = settings.meta_webhook_verify_token
    if not expected_token:
        logger.warning("meta_webhook_verify_no_token")
        return None

    if token != expected_token:
        logger.warning(
            "meta_webhook_verify_token_mismatch",
            expected=expected_token[:10],
            received=token[:10],
        )
        return None

    logger.info("meta_webhook_verified", mode=mode)
    return challenge


def verify_whatsapp_webhook(
    mode: str,
    token: str,
    challenge: str,
) -> str | None:
    """Verify WhatsApp webhook verification request.

    Args:
        mode: Webhook verification mode
        token: The verify token
        challenge: Challenge to echo back

    Returns:
        Challenge string if verification passes, None otherwise
    """
    expected_token = settings.whatsapp_webhook_verify_token
    if not expected_token:
        logger.warning("whatsapp_webhook_verify_no_token")
        return None

    if token != expected_token:
        logger.warning(
            "whatsapp_webhook_verify_token_mismatch",
            expected=expected_token[:10],
            received=token[:10],
        )
        return None

    logger.info("whatsapp_webhook_verified", mode=mode)
    return challenge
