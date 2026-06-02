"""Moyasar payment gateway service for Saudi Arabia (KSA).

Uses the Moyasar Invoices API to create a hosted payment page (card, mada,
Apple Pay, STC Pay). The flow mirrors Kashier's session model:

1. Backend creates an invoice via POST /v1/invoices
2. Response includes a hosted ``url`` the storefront redirects to
3. Customer pays on Moyasar's page → Moyasar POSTs a webhook to our
   callback and redirects the browser to our success/back URL
4. The webhook marks the order paid

Auth is HTTP Basic with the secret key as the username and an empty
password. Amounts are in the smallest currency unit (halalas); since we
already store SAR in cents (1 SAR = 100 halalas), the integer amount maps
through unchanged.

API docs: https://docs.moyasar.com/
"""

import json
import logging

import httpx

from src.core.interfaces.services.payment_service import (
    IPaymentService,
    PaymentIntent,
    PaymentProvider,
    PaymentResult,
    RefundResult,
)

logger = logging.getLogger(__name__)

MOYASAR_API_BASE = "https://api.moyasar.com"

# Moyasar invoice/payment statuses that mean the funds were captured.
_PAID_STATUSES = {"paid"}


class MoyasarPaymentService(IPaymentService):
    """Moyasar payment service using the Invoices API.

    Args:
        secret_key: Moyasar secret API key (sk_test_… / sk_live_…). Used as
            the HTTP Basic username.
        publishable_key: Publishable key (pk_…) — not required server-side
            but accepted for completeness / future client-token flows.
        webhook_secret: The shared ``secret_token`` configured on the
            Moyasar webhook; used to authenticate inbound webhooks.
        currency: Capture currency (defaults to SAR).
    """

    def __init__(
        self,
        secret_key: str | None = None,
        publishable_key: str | None = None,
        webhook_secret: str | None = None,
        currency: str | None = None,
    ):
        self._secret_key = secret_key
        self._publishable_key = publishable_key
        self._webhook_secret = webhook_secret
        self._currency = currency or "SAR"

    @property
    def provider(self) -> PaymentProvider:
        return PaymentProvider.MOYASAR

    def _auth(self) -> tuple[str, str]:
        """HTTP Basic auth: secret key as username, empty password."""
        return (self._secret_key or "", "")

    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Create a Moyasar invoice and return its hosted payment URL.

        Args:
            amount: Amount in the smallest unit (halalas == our cents).
            currency: Currency code (defaults to SAR).
            customer_email: Buyer email, attached to invoice metadata.
            metadata: Must contain ``order_id``; may carry ``callback_url``,
                ``success_url`` and ``back_url`` overrides.

        Returns:
            PaymentIntent where ``client_secret`` is the hosted invoice URL
            the storefront redirects the customer to.
        """
        if not self._secret_key:
            raise ValueError("Moyasar secret key is required")

        metadata = metadata or {}
        order_id = str(metadata.get("order_id", ""))
        currency = (currency or self._currency).upper()

        callback_url = metadata.get(
            "callback_url",
            "https://numueg.app/api/v1/webhooks/moyasar/callback",
        )
        success_url = metadata.get(
            "success_url",
            f"https://numueg.app/api/v1/webhooks/moyasar/redirect?order_id={order_id}",
        )
        back_url = metadata.get(
            "back_url",
            f"https://numueg.app/api/v1/webhooks/moyasar/redirect?order_id={order_id}",
        )

        # Moyasar metadata values must be scalars; keep it flat.
        invoice_metadata = {"order_id": order_id}
        if customer_email:
            invoice_metadata["email"] = customer_email

        payload = {
            "amount": int(amount),
            "currency": currency,
            "description": metadata.get("description") or f"Order {order_id}",
            "callback_url": callback_url,
            "success_url": success_url,
            "back_url": back_url,
            "metadata": invoice_metadata,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MOYASAR_API_BASE}/v1/invoices",
                json=payload,
                auth=self._auth(),
                timeout=30.0,
            )

        if response.status_code not in (200, 201):
            logger.error("Moyasar invoice creation failed: %s", response.text)
            raise ValueError(f"Failed to create Moyasar invoice: {response.text}")

        data = response.json()
        invoice_id = data.get("id", "")
        invoice_url = data.get("url", "")

        logger.info("Moyasar invoice created: id=%s order=%s", invoice_id, order_id)

        return PaymentIntent(
            id=invoice_id,
            client_secret=invoice_url,
            amount=int(amount),
            currency=currency,
            status="pending",
            provider=PaymentProvider.MOYASAR,
        )

    async def _get_invoice(self, invoice_id: str) -> dict | None:
        """Fetch an invoice; returns the parsed body or None on failure."""
        if not self._secret_key:
            return None
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{MOYASAR_API_BASE}/v1/invoices/{invoice_id}",
                auth=self._auth(),
                timeout=30.0,
            )
        if response.status_code != 200:
            logger.warning(
                "Moyasar invoice fetch failed (%s): %s",
                response.status_code,
                response.text,
            )
            return None
        return response.json()

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        """Check whether the invoice has been paid."""
        invoice = await self._get_invoice(payment_intent_id)
        if invoice is None:
            return PaymentResult(
                success=False, error_message="Failed to fetch invoice status"
            )
        status = (invoice.get("status") or "").lower()
        return PaymentResult(
            success=status in _PAID_STATUSES,
            payment_id=payment_intent_id,
            error_message=None if status in _PAID_STATUSES else f"Status: {status}",
        )

    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        # Moyasar invoices auto-capture; confirmation is the capture check.
        return await self.confirm_payment(payment_intent_id)

    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
        """Void an unpaid invoice."""
        if not self._secret_key:
            return PaymentResult(
                success=False, error_message="Secret key not configured"
            )
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{MOYASAR_API_BASE}/v1/invoices/{payment_intent_id}/cancel",
                auth=self._auth(),
                timeout=30.0,
            )
        if response.status_code == 200:
            return PaymentResult(success=True, payment_id=payment_intent_id)
        return PaymentResult(
            success=False,
            error_message=f"Cancel failed: {response.text}",
            error_code="CANCEL_FAILED",
        )

    async def refund_payment(
        self,
        payment_id: str,
        amount: int | None = None,
    ) -> RefundResult:
        """Refund a captured payment (full or partial).

        Args:
            payment_id: The Moyasar *payment* id (from the webhook), not the
                invoice id.
            amount: Partial refund amount in halalas; full refund if None.
        """
        if not self._secret_key:
            return RefundResult(
                success=False, error_message="Secret key not configured"
            )

        body = {"amount": int(amount)} if amount is not None else {}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MOYASAR_API_BASE}/v1/payments/{payment_id}/refund",
                json=body,
                auth=self._auth(),
                timeout=30.0,
            )
        if response.status_code in (200, 201):
            data = response.json()
            return RefundResult(success=True, refund_id=data.get("id") or payment_id)
        logger.error("Moyasar refund failed: %s", response.text)
        return RefundResult(
            success=False, error_message=f"Refund failed: {response.text}"
        )

    async def get_payment_status(self, payment_id: str) -> str:
        result = await self.confirm_payment(payment_id)
        return "paid" if result.success else "pending"

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Authenticate a Moyasar webhook.

        Moyasar does not HMAC-sign webhooks; instead each delivery carries a
        ``secret_token`` field that must equal the token configured on the
        webhook. We compare it (constant-time) against the merchant's
        ``webhook_secret``. The ``signature`` argument is accepted for
        interface symmetry and used as a fallback expected-token source.

        Returns the parsed payload dict when authentic, else None.
        """
        try:
            raw = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            logger.warning("moyasar_webhook_invalid_json")
            return None

        expected = self._webhook_secret or signature or ""
        provided = raw.get("secret_token") or ""

        # If no secret is configured we cannot authenticate — fail closed.
        if not expected:
            logger.warning("moyasar_webhook_no_secret_configured")
            return None

        import hmac

        if hmac.compare_digest(str(provided), str(expected)):
            return raw

        logger.warning("moyasar_webhook_secret_mismatch")
        return None
