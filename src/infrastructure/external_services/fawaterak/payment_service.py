"""Fawaterak payment service implementation for Egyptian market.

Fawaterak is an Egyptian payment aggregator supporting:
- Credit/Debit cards (Visa, Mastercard, Meeza)
- Mobile wallets (Vodafone Cash, Orange Cash, Etisalat Cash)
- Fawry reference payments
- Aman, Masary

API Documentation: https://fawaterak-api.readme.io/reference/overview

Integration flow:
1. create_payment_intent() -> POST /api/v2/createInvoiceLink
2. Customer is redirected to the returned payment URL
3. Webhook notification received on payment completion
4. verify_webhook_signature() -> Validate HMAC SHA256
"""

import base64
import hashlib
import hmac
import logging
from typing import Any

import httpx

from src.core.exceptions import PaymentError
from src.core.interfaces.services.payment_service import (
    IPaymentService,
    PaymentIntent,
    PaymentProvider,
    PaymentResult,
    RefundResult,
)

logger = logging.getLogger(__name__)

FAWATERAK_STAGING_BASE = "https://staging.fawaterk.com/api/v2"
FAWATERAK_PRODUCTION_BASE = "https://app.fawaterk.com/api/v2"


async def get_merchant_fawaterak_credentials(store_settings: dict) -> dict:
    """Decrypt and return a merchant's Fawaterak credentials from store settings.

    Returns:
        dict with keys: api_key, vendor_key, environment

    Raises:
        PaymentError: If credentials are not configured or decryption fails.
    """
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    fawaterak_settings = (store_settings or {}).get("payment", {}).get("fawaterak", {})

    if not fawaterak_settings.get("encrypted_credentials"):
        raise PaymentError(
            "Fawaterak credentials not configured for this store. "
            "Please configure payment gateway in store settings."
        )

    secrets_manager = get_secrets_manager()
    key_id = fawaterak_settings["encryption_key_id"]
    encrypted = base64.b64decode(fawaterak_settings["encrypted_credentials"])

    try:
        return await secrets_manager.decrypt(encrypted, key_id)
    except Exception as e:
        logger.error(f"Failed to decrypt Fawaterak credentials: {e}")
        raise PaymentError(
            "Failed to read payment credentials. Please re-save them."
        ) from e


class FawaterakPaymentService(IPaymentService):
    """Fawaterak payment service using the Invoice Link API.

    Flow:
    1. create_payment_intent() - POST to createInvoiceLink
    2. Frontend redirects customer to the returned URL
    3. Customer selects payment method and completes payment
    4. Webhook notification received
    5. verify_webhook_signature() - Validate HMAC and process
    """

    def __init__(
        self,
        api_key: str | None = None,
        vendor_key: str | None = None,
        environment: str = "staging",
    ) -> None:
        self.api_key = api_key
        self.vendor_key = vendor_key
        self.environment = environment
        self.base_url = (
            FAWATERAK_PRODUCTION_BASE
            if environment == "production"
            else FAWATERAK_STAGING_BASE
        )

    @property
    def provider(self) -> PaymentProvider:
        return PaymentProvider.FAWATERAK

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Create a Fawaterak invoice link.

        Args:
            amount: Amount in cents (will be converted to units for Fawaterak)
            currency: Currency code (EGP, USD, etc.)
            customer_email: Customer email
            metadata: Should include order_id, billing_data, items

        Returns:
            PaymentIntent with payment URL as client_secret
        """
        if not self.api_key:
            raise PaymentError("Fawaterak API key not configured")

        metadata = metadata or {}
        billing_data = metadata.get("billing_data", {})
        items = metadata.get("items", [])
        order_id = metadata.get("order_id", "")

        # Fawaterak expects amounts in currency units (not cents)
        amount_units = amount / 100

        # Build cart items for Fawaterak
        cart_items = []
        if items:
            for item in items:
                cart_items.append({
                    "name": item.get("name", "Product"),
                    "price": str(item.get("price", amount_units)),
                    "quantity": str(item.get("quantity", 1)),
                })
        else:
            cart_items.append({
                "name": f"Order {order_id}",
                "price": str(amount_units),
                "quantity": "1",
            })

        # Build customer data
        first_name = billing_data.get("first_name", "Customer")
        last_name = billing_data.get("last_name", "")

        payload: dict[str, Any] = {
            "cartTotal": str(amount_units),
            "currency": currency.upper(),
            "customer": {
                "first_name": first_name,
                "last_name": last_name or first_name,
                "email": customer_email or billing_data.get("email", ""),
                "phone": billing_data.get("phone_number", ""),
                "address": billing_data.get("street", ""),
            },
            "cartItems": cart_items,
            "payLoad": order_id,
            "sendEmail": False,
            "sendSMS": False,
            "redirectionUrls": {},
        }

        # Add redirect URLs from metadata if provided
        redirect_urls = metadata.get("redirect_urls", {})
        if redirect_urls:
            payload["redirectionUrls"] = {
                "successUrl": redirect_urls.get("success_url", ""),
                "failUrl": redirect_urls.get("fail_url", ""),
                "pendingUrl": redirect_urls.get("pending_url", ""),
            }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/createInvoiceLink",
                json=payload,
                headers=self._headers(),
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Fawaterak invoice creation failed: {response.text}")
                raise PaymentError("Failed to create payment with Fawaterak")

            data = response.json()

        if data.get("status") != "success":
            logger.error(f"Fawaterak invoice creation failed: {data}")
            raise PaymentError(
                data.get("message", "Failed to create payment with Fawaterak")
            )

        result_data = data.get("data", {})
        invoice_id = str(result_data.get("invoiceId", ""))
        payment_url = result_data.get("url", "")

        return PaymentIntent(
            id=invoice_id,
            client_secret=payment_url,
            amount=amount,
            currency=currency.upper(),
            status="pending",
            provider=PaymentProvider.FAWATERAK,
        )

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        """Check payment status via Fawaterak API."""
        if not self.api_key:
            return PaymentResult(success=False, error_message="API key not configured")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/getInvoiceData/{payment_intent_id}",
                headers=self._headers(),
                timeout=30.0,
            )

            if response.status_code != 200:
                return PaymentResult(
                    success=False,
                    error_message="Failed to fetch payment status",
                )

            data = response.json()
            invoice_data = data.get("data", {})
            is_paid = invoice_data.get("paid") == 1

            return PaymentResult(
                success=is_paid,
                payment_id=payment_intent_id,
                error_message=None if is_paid else "Payment not yet completed",
            )

    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        """Capture payment - Fawaterak auto-captures."""
        return await self.confirm_payment(payment_intent_id)

    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
        """Cancel a Fawaterak payment.

        Fawaterak doesn't have a direct cancel API; invoices expire naturally.
        """
        logger.warning(
            f"Fawaterak cancel requested for {payment_intent_id} - "
            "invoices expire automatically"
        )
        return PaymentResult(
            success=True,
            payment_id=payment_intent_id,
        )

    async def refund_payment(
        self,
        payment_id: str,
        amount: int | None = None,
    ) -> RefundResult:
        """Refund a Fawaterak payment.

        Fawaterak refunds are typically handled through the merchant portal.
        """
        logger.warning(
            f"Fawaterak refund requested for {payment_id} - "
            "please process via the Fawaterak merchant portal"
        )
        return RefundResult(
            success=False,
            error_message="Fawaterak refunds must be processed via the merchant portal",
        )

    async def get_payment_status(self, payment_id: str) -> str:
        """Get Fawaterak payment status."""
        if not self.api_key:
            raise PaymentError("API key not configured")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/getInvoiceData/{payment_id}",
                headers=self._headers(),
                timeout=30.0,
            )

            if response.status_code != 200:
                raise PaymentError("Failed to get payment status")

            data = response.json()
            invoice_data = data.get("data", {})
            is_paid = invoice_data.get("paid") == 1

            if is_paid:
                return "paid"
            return "pending"

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify Fawaterak webhook HMAC SHA256 signature.

        For paid transactions:
        queryParam = "InvoiceId={invoice_id}&InvoiceKey={invoice_key}&PaymentMethod={payment_method}"
        hash = HMAC-SHA256(queryParam, vendor_key)

        For cancelled transactions:
        queryParam = "referenceId={referenceId}&PaymentMethod={paymentMethod}"
        hash = HMAC-SHA256(queryParam, vendor_key)
        """
        if not self.vendor_key:
            logger.warning("Fawaterak vendor key not configured, skipping verification")
            return None

        try:
            import json

            data = json.loads(payload)

            # Determine event type and build verification string
            hash_key = data.get("hashKey", "")

            if "invoice_id" in data:
                # Paid transaction webhook
                query_param = (
                    f"InvoiceId={data.get('invoice_id', '')}"
                    f"&InvoiceKey={data.get('invoice_key', '')}"
                    f"&PaymentMethod={data.get('payment_method', '')}"
                )
            elif "referenceId" in data:
                # Cancelled transaction webhook
                query_param = (
                    f"referenceId={data.get('referenceId', '')}"
                    f"&PaymentMethod={data.get('paymentMethod', '')}"
                )
            else:
                # Refund or unknown - no hash verification
                return data

            expected = hmac.new(
                self.vendor_key.encode(),
                query_param.encode(),
                hashlib.sha256,
            ).hexdigest()

            if hmac.compare_digest(expected, hash_key):
                return data
            else:
                logger.warning("Fawaterak webhook signature mismatch")
                return None

        except Exception as e:
            logger.error(f"Fawaterak webhook verification error: {e}")
            return None
