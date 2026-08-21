"""Meta webhook signature verification."""

import base64
import hashlib
import hmac
import json

from src.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


def parse_signed_request(
    signed_request: str,
    app_secret: str | None = None,
) -> dict | None:
    """Parse and verify a Meta ``signed_request`` payload.

    Used by the deauthorize and data-deletion callbacks. Format is
    ``<base64url signature>.<base64url json payload>`` signed with
    HMAC-SHA256 over the raw payload segment using the app secret.

    Returns the decoded payload dict, or None if the signature is
    invalid or the request is malformed.
    """
    secret = app_secret or settings.meta_app_secret
    if not secret:
        logger.warning("meta_signed_request_no_secret")
        return None
    if not signed_request or "." not in signed_request:
        return None

    encoded_sig, payload = signed_request.split(".", 1)
    try:
        sig = base64.urlsafe_b64decode(encoded_sig + "=" * (-len(encoded_sig) % 4))
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError):
        logger.warning("meta_signed_request_malformed")
        return None

    expected = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(sig, expected):
        logger.warning("meta_signed_request_invalid_signature")
        return None

    if not isinstance(data, dict):
        return None
    return data


def verify_x_hub_signature(
    payload: bytes | str,
    signature: str,
    app_secret: str | None = None,
) -> bool:
    """Verify X-Hub-Signature-256 header from Meta webhooks.

    Facebook Page events are signed with the Facebook app secret;
    Instagram-product deliveries are signed with the linked Instagram
    app's own secret — a valid HMAC under either accepts the payload.

    Args:
        payload: Raw request body (bytes or str)
        signature: The X-Hub-Signature-256 header value
        app_secret: Explicit secret override (skips the settings pair)

    Returns:
        True if signature is valid, False otherwise
    """
    if app_secret:
        secrets = [app_secret]
    else:
        secrets = [
            s for s in (settings.meta_app_secret, settings.meta_ig_app_secret) if s
        ]
    if not secrets:
        logger.warning("meta_signature_verify_no_secret")
        return False

    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    expected_signatures = [
        "sha256="
        + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        for secret in secrets
    ]

    if not any(
        hmac.compare_digest(signature, expected) for expected in expected_signatures
    ):
        logger.warning(
            "meta_signature_invalid",
            received_prefix=signature[:20],
            secrets_tried=len(expected_signatures),
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
