"""Kashier payment gateway service for Egypt.

Uses the Payment Sessions API (v3) to create server-side sessions
that render as embedded iframes on the storefront.

API Docs: https://developers.kashier.io/payment/payment-sessions
"""

import hashlib
import hmac
import json
import logging

import httpx

from src.config import settings
from src.core.interfaces.services.payment_service import (
    IPaymentService,
    PaymentIntent,
    PaymentProvider,
    PaymentResult,
    RefundResult,
)

logger = logging.getLogger(__name__)

KASHIER_API_BASE = "https://api.kashier.io"
KASHIER_TEST_API_BASE = "https://test-api.kashier.io"


class KashierPaymentService(IPaymentService):
    """Kashier payment service using the Payment Sessions API.

    Flow:
    1. Backend creates a payment session via POST /v3/payment/sessions
    2. Response includes sessionUrl for iframe embedding
    3. Frontend renders iframe with sessionUrl
    4. Customer pays → Kashier sends webhook to serverWebhook URL
    """

    def __init__(
        self,
        mid: str | None = None,
        api_key: str | None = None,
        secret_key: str | None = None,
        mode: str | None = None,
        currency: str | None = None,
    ):
        self._mid = mid or settings.kashier_mid
        self._api_key = api_key or settings.kashier_api_key
        self._secret_key = secret_key
        self._mode = mode or settings.kashier_mode or "test"
        self._currency = currency or settings.kashier_currency or "EGP"

    @property
    def provider(self) -> PaymentProvider:
        return PaymentProvider.KASHIER

    def _get_api_base(self) -> str:
        return KASHIER_TEST_API_BASE if self._mode == "test" else KASHIER_API_BASE

    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Create a Kashier payment session.

        Args:
            amount: Amount in cents (converted to pounds for Kashier).
            currency: Currency code (default EGP).
            customer_email: Customer email for receipts.
            metadata: Must contain 'order_id' and optionally 'webhook_url'.

        Returns:
            PaymentIntent where client_secret = sessionUrl for iframe.
        """
        if not self._api_key:
            raise ValueError("Kashier API key is required")

        metadata = metadata or {}
        order_id = metadata.get("order_id", "")
        amount_str = f"{amount / 100:.2f}"
        currency = currency or self._currency

        # Build webhook URL
        webhook_url = metadata.get(
            "webhook_url",
            "https://numueg.app/api/v1/webhooks/kashier/callback",
        )

        # Build merchant redirect URL
        redirect_url = metadata.get(
            "redirect_url",
            f"https://numueg.app/api/v1/webhooks/kashier/redirect?order_id={order_id}",
        )

        session_payload = {
            "merchantId": self._mid,
            "amount": amount_str,
            "currency": currency,
            "paymentType": "credit",
            "order": order_id,
            "type": "one-time",
            "allowedMethods": "card,wallet",
            "enable3DS": True,
            "display": "en",
            "serverWebhook": webhook_url,
            "merchantRedirect": redirect_url,
            "failureRedirect": True,
        }

        if customer_email:
            session_payload["customer"] = {"email": customer_email}

        # Determine auth headers — prefer secret_key, fall back to api_key
        headers = {
            "Content-Type": "application/json",
            "api-key": self._api_key,
        }
        if self._secret_key:
            headers["Authorization"] = self._secret_key

        api_base = self._get_api_base()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{api_base}/v3/payment/sessions",
                json=session_payload,
                headers=headers,
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Kashier session creation failed: {response.text}")
                raise ValueError(
                    f"Failed to create Kashier payment session: {response.text}"
                )

            data = response.json()

        session_id = data.get("_id", "")
        session_url = data.get("sessionUrl", "")

        if not session_url:
            # Build it manually if not returned
            mode_param = "test" if self._mode == "test" else "live"
            session_url = (
                f"https://payments.kashier.io/session/{session_id}?mode={mode_param}"
            )

        logger.info(
            f"Kashier session created: session_id={session_id}, order={order_id}"
        )

        return PaymentIntent(
            id=session_id,
            client_secret=session_url,  # sessionUrl for iframe
            amount=amount,
            currency=currency,
            status="pending",
            provider=PaymentProvider.KASHIER,
        )

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        """Check session payment status."""
        if not self._api_key:
            return PaymentResult(success=False, error_message="API key not configured")

        api_base = self._get_api_base()
        headers = {"api-key": self._api_key}
        if self._secret_key:
            headers["Authorization"] = self._secret_key

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{api_base}/v3/payment/sessions/{payment_intent_id}/payment",
                headers=headers,
                timeout=30.0,
            )

            if response.status_code != 200:
                return PaymentResult(
                    success=False, error_message="Failed to check payment status"
                )

            data = response.json()
            status = data.get("paymentStatus", "")
            return PaymentResult(
                success=status == "SUCCESS",
                payment_id=payment_intent_id,
                error_message=None if status == "SUCCESS" else f"Status: {status}",
            )

    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        return await self.confirm_payment(payment_intent_id)

    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
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
        return RefundResult(
            success=False,
            error_message="Kashier refunds must be processed via the merchant dashboard",
        )

    async def get_payment_status(self, payment_id: str) -> str:
        result = await self.confirm_payment(payment_id)
        return "paid" if result.success else "pending"

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify Kashier webhook signature using HMAC SHA256."""
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
        final_string = query_string[1:]

        calculated_sig = hmac.new(
            self._api_key.encode("utf-8"),
            final_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if hmac.compare_digest(calculated_sig, signature):
            return data

        logger.warning("kashier_webhook_signature_mismatch")
        return None
