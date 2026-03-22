"""Kashier payment gateway service for Egypt.

Kashier uses a hash-based approach where the backend generates
an HMAC SHA256 hash, and the frontend uses it to render the
Kashier payment form or construct the HPP URL.

Base URL: https://payments.kashier.io
"""

import hashlib
import hmac
import json
import uuid

from src.config import settings
from src.config.logging_config import get_logger
from src.core.interfaces.services.payment_service import (
    IPaymentService,
    PaymentIntent,
    PaymentProvider,
    PaymentResult,
    RefundResult,
)

logger = get_logger(__name__)


class KashierPaymentService(IPaymentService):
    """Kashier payment service implementation.

    Kashier uses a client-side hash-based approach where the backend
    generates an HMAC SHA256 hash that is passed to the frontend for
    rendering the payment form via the Kashier JS SDK or HPP URL.
    """

    PAYMENTS_BASE_URL = "https://payments.kashier.io"

    def __init__(
        self,
        mid: str | None = None,
        api_key: str | None = None,
        mode: str | None = None,
        currency: str | None = None,
    ):
        self._mid = mid or settings.kashier_mid
        self._api_key = api_key or settings.kashier_api_key
        self._mode = mode or settings.kashier_mode
        self._currency = currency or settings.kashier_currency

    @property
    def provider(self) -> PaymentProvider:
        return PaymentProvider.KASHIER

    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Generate Kashier payment hash for frontend form rendering.

        The hash is computed as:
            path = "/?payment={mid}.{order_id}.{amount}.{currency}"
            hash = HMAC-SHA256(api_key, path)

        Args:
            amount: Amount in cents (converted to pounds for Kashier).
            currency: Currency code (default EGP).
            customer_email: Optional customer email.
            metadata: Must contain 'order_id' key for merchant order reference.

        Returns:
            PaymentIntent with client_secret containing the HMAC hash.
        """
        if not self._mid or not self._api_key:
            raise ValueError("Kashier MID and API key are required")

        order_id = (metadata or {}).get("order_id", str(uuid.uuid4()))
        # Kashier expects amount in pounds (not cents)
        amount_pounds = f"{amount / 100:.2f}"
        currency = currency or self._currency or "EGP"

        # Generate HMAC SHA256 hash
        path = f"/?payment={self._mid}.{order_id}.{amount_pounds}.{currency}"
        hash_value = hmac.new(
            self._api_key.encode("utf-8"),
            path.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        logger.info(
            "kashier_payment_intent_created",
            order_id=order_id,
            amount_cents=amount,
            currency=currency,
            mode=self._mode,
        )

        return PaymentIntent(
            id=order_id,
            client_secret=hash_value,
            amount=amount,
            currency=currency,
            status="pending",
            provider=PaymentProvider.KASHIER,
        )

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        """Not applicable — payment confirmation happens via webhook."""
        return PaymentResult(
            success=False,
            error_message="Kashier payments are confirmed via webhook callback",
            error_code="NOT_APPLICABLE",
        )

    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        """Not applicable — no separate capture step."""
        return PaymentResult(
            success=False,
            error_message="Kashier does not support separate capture",
            error_code="NOT_SUPPORTED",
        )

    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
        """Not applicable — no server-side cancel API."""
        return PaymentResult(
            success=False,
            error_message="Kashier does not support server-side cancellation",
            error_code="NOT_SUPPORTED",
        )

    async def refund_payment(
        self,
        payment_id: str,
        amount: int | None = None,
    ) -> RefundResult:
        """Refund via Kashier.

        Kashier refunds are currently processed via the merchant dashboard.
        """
        return RefundResult(
            success=False,
            error_message="Kashier refunds must be processed via the merchant dashboard",
        )

    async def get_payment_status(self, payment_id: str) -> str:
        """Get payment status."""
        return "unknown"

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify Kashier webhook signature using HMAC SHA256.

        Kashier webhook signature is computed by concatenating specific
        fields from the payload into a query string (without leading &),
        then computing HMAC SHA256 with the API_KEY.

        Fields (in order):
            paymentStatus, cardDataToken, maskedCard, merchantOrderId,
            orderId, cardBrand, orderReference, transactionId, amount, currency

        Args:
            payload: Raw request body bytes.
            signature: The signature header value from Kashier.

        Returns:
            Parsed payload dict if signature is valid, None if invalid.
        """
        if not self._api_key:
            logger.error("kashier_webhook_no_api_key")
            return None

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            logger.warning("kashier_webhook_invalid_json")
            return None

        # Reconstruct the query string in the exact field order
        query_string = (
            f"&paymentStatus={data.get('paymentStatus', '')}"
            f"&cardDataToken={data.get('cardDataToken', '')}"
            f"&maskedCard={data.get('maskedCard', '')}"
            f"&merchantOrderId={data.get('merchantOrderId', '')}"
            f"&orderId={data.get('orderId', '')}"
            f"&cardBrand={data.get('cardBrand', '')}"
            f"&orderReference={data.get('orderReference', '')}"
            f"&transactionId={data.get('transactionId', '')}"
            f"&amount={data.get('amount', '')}"
            f"&currency={data.get('currency', '')}"
        )
        # Remove leading '&'
        final_string = query_string[1:]

        calculated_sig = hmac.new(
            self._api_key.encode("utf-8"),
            final_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if hmac.compare_digest(calculated_sig, signature):
            logger.info(
                "kashier_webhook_signature_valid",
                order_id=data.get("merchantOrderId"),
            )
            return data

        logger.warning(
            "kashier_webhook_signature_mismatch",
            order_id=data.get("merchantOrderId"),
        )
        return None
