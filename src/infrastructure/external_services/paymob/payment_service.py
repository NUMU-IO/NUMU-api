"""Paymob payment service implementation for Egyptian market.

Paymob is Egypt's leading payment gateway supporting:
- Card payments (Visa, Mastercard, Meeza)
- Mobile wallets (Vodafone Cash, Orange Cash, Etisalat Cash, WE Pay)

API Documentation: https://docs.paymob.com/
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

# Paymob API base URLs
PAYMOB_API_BASE = "https://accept.paymob.com/api"
PAYMOB_INTENTION_API_BASE = "https://accept.paymob.com/v1/intention/"


async def get_merchant_paymob_credentials(store_settings: dict) -> dict:
    """Decrypt and return a merchant's Paymob credentials from store settings.

    Returns:
        dict with keys: secret_key, public_key, hmac_secret,
        card_integration_id, wallet_integration_id

    Raises:
        PaymentError: If credentials are not configured or decryption fails.
    """
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    paymob_settings = (store_settings or {}).get("payment", {}).get("paymob", {})

    if not paymob_settings.get("encrypted_credentials"):
        raise PaymentError(
            "Paymob credentials not configured for this store. "
            "Please configure payment gateway in store settings."
        )

    secrets_manager = get_secrets_manager()
    key_id = paymob_settings["encryption_key_id"]
    encrypted = base64.b64decode(paymob_settings["encrypted_credentials"])

    try:
        return await secrets_manager.decrypt(encrypted, key_id)
    except Exception as e:
        logger.error(f"Failed to decrypt Paymob credentials: {e}")
        raise PaymentError(
            "Failed to read payment credentials. Please re-save them."
        ) from e


class PaymobPaymentService(IPaymentService):
    """Paymob payment service using the Intention API.

    Flow (new API):
    1. create_payment_intent() - Single POST to create intention
    2. Frontend renders Pixel Embedded with client_secret + public_key
    3. Customer completes payment inline
    4. Webhook notification received
    5. verify_webhook_signature() - Validate and process
    """

    def __init__(
        self,
        secret_key: str | None = None,
        public_key: str | None = None,
        hmac_secret: str | None = None,
        card_integration_id: str | None = None,
        wallet_integration_id: str | None = None,
    ) -> None:
        self.secret_key = secret_key
        self.public_key = public_key
        self.hmac_secret = hmac_secret
        self.card_integration_id = card_integration_id
        self.wallet_integration_id = wallet_integration_id

    @property
    def provider(self) -> PaymentProvider:
        """Get the payment provider."""
        return PaymentProvider.PAYMOB

    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Create a Paymob payment intention (new Intention API).

        Args:
            amount: Amount in cents
            currency: Currency code (EGP recommended)
            customer_email: Customer email
            metadata: Should include billing_data and order_id

        Returns:
            PaymentIntent with client_secret for Pixel Embedded
        """
        if not self.secret_key:
            raise PaymentError("Paymob secret key not configured")
        if not self.card_integration_id:
            raise PaymentError("Paymob card integration ID not configured")

        metadata = metadata or {}
        billing_data = metadata.get("billing_data", {})
        if customer_email:
            billing_data["email"] = customer_email

        # Build payment method integration IDs list
        payment_methods = [int(self.card_integration_id)]
        if self.wallet_integration_id:
            payment_methods.append(int(self.wallet_integration_id))

        # Default billing fields required by Paymob
        default_billing = {
            "apartment": "NA",
            "email": "customer@example.com",
            "floor": "NA",
            "first_name": "Customer",
            "street": "NA",
            "building": "NA",
            "phone_number": "+201000000000",
            "shipping_method": "NA",
            "postal_code": "NA",
            "city": "NA",
            "country": "EG",
            "last_name": "Customer",
            "state": "NA",
        }
        billing = {**default_billing, **billing_data}

        our_order_id = metadata.get("order_id")
        payload = {
            "amount": amount,
            "currency": currency.upper(),
            "payment_methods": payment_methods,
            "billing_data": billing,
            "merchant_order_id": our_order_id,
            "special_reference": our_order_id,
            "extras": {
                "order_id": our_order_id,
            },
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                PAYMOB_INTENTION_API_BASE,
                json=payload,
                headers={"Authorization": f"Token {self.secret_key}"},
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Paymob intention creation failed: {response.text}")
                raise PaymentError("Failed to create payment with Paymob")

            data = response.json()

        intention_id = str(
            data.get("intention_detail", {}).get("id", data.get("id", ""))
        )
        client_secret = data.get("client_secret", "")

        return PaymentIntent(
            id=intention_id,
            client_secret=client_secret,
            amount=amount,
            currency=currency.upper(),
            status="pending",
            provider=PaymentProvider.PAYMOB,
        )

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        """Check payment status via Paymob API.

        Paymob payments are confirmed via webhooks; this polls the status.
        """
        if not self.secret_key:
            return PaymentResult(
                success=False, error_message="Secret key not configured"
            )

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://accept.paymob.com/v1/intention/{payment_intent_id}/",
                headers={"Authorization": f"Token {self.secret_key}"},
                timeout=30.0,
            )

            if response.status_code != 200:
                return PaymentResult(
                    success=False,
                    error_message="Failed to fetch payment status",
                )

            data = response.json()
            status = data.get("intention_detail", {}).get("status", "")
            is_paid = status in ("confirmed", "captured")

            return PaymentResult(
                success=is_paid,
                payment_id=payment_intent_id,
                error_message=None if is_paid else f"Payment status: {status}",
            )

    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        """Capture payment — Paymob auto-captures by default."""
        return await self.confirm_payment(payment_intent_id)

    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
        """Cancel/void a Paymob payment."""
        if not self.secret_key:
            return PaymentResult(
                success=False, error_message="Secret key not configured"
            )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYMOB_API_BASE}/acceptance/void_refund/void",
                headers={"Authorization": f"Token {self.secret_key}"},
                json={"transaction_id": payment_intent_id},
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Paymob void failed: {response.text}")
                return PaymentResult(
                    success=False,
                    error_message="Failed to void payment",
                )

            return PaymentResult(success=True, payment_id=payment_intent_id)

    async def refund_payment(
        self,
        payment_id: str,
        amount: int | None = None,
    ) -> RefundResult:
        """Refund a Paymob payment."""
        if not self.secret_key:
            return RefundResult(
                success=False, error_message="Secret key not configured"
            )

        refund_data: dict[str, Any] = {"transaction_id": payment_id}
        if amount:
            refund_data["amount_cents"] = amount

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYMOB_API_BASE}/acceptance/void_refund/refund",
                headers={"Authorization": f"Token {self.secret_key}"},
                json=refund_data,
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Paymob refund failed: {response.text}")
                return RefundResult(
                    success=False,
                    error_message="Failed to refund payment",
                )

            data = response.json()
            return RefundResult(success=True, refund_id=str(data.get("id")))

    async def get_payment_status(self, payment_id: str) -> str:
        """Get Paymob payment status."""
        if not self.secret_key:
            raise PaymentError("Secret key not configured")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://accept.paymob.com/v1/intention/{payment_id}/",
                headers={"Authorization": f"Token {self.secret_key}"},
                timeout=30.0,
            )

            if response.status_code != 200:
                raise PaymentError("Failed to get payment status")

            data = response.json()
            status = data.get("intention_detail", {}).get("status", "pending")
            if status in ("confirmed", "captured"):
                return "paid"
            elif status in ("voided", "cancelled"):
                return "cancelled"
            else:
                return "pending"

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify Paymob webhook HMAC signature.

        Paymob uses HMAC-SHA512 for webhook verification.
        The signature is calculated from specific fields in order.
        """
        if not self.hmac_secret:
            logger.warning("Paymob HMAC secret not configured, skipping verification")
            return None

        try:
            import json

            data = json.loads(payload)

            # Paymob HMAC is calculated from specific fields in this order
            obj = data.get("obj", {})
            concatenated = "".join([
                str(obj.get("amount_cents", "")),
                str(obj.get("created_at", "")),
                str(obj.get("currency", "")),
                str(obj.get("error_occured", "")),
                str(obj.get("has_parent_transaction", "")),
                str(obj.get("id", "")),
                str(obj.get("integration_id", "")),
                str(obj.get("is_3d_secure", "")),
                str(obj.get("is_auth", "")),
                str(obj.get("is_capture", "")),
                str(obj.get("is_refunded", "")),
                str(obj.get("is_standalone_payment", "")),
                str(obj.get("is_voided", "")),
                str(obj.get("order", {}).get("id", "")),
                str(obj.get("owner", "")),
                str(obj.get("pending", "")),
                str(obj.get("source_data", {}).get("pan", "")),
                str(obj.get("source_data", {}).get("sub_type", "")),
                str(obj.get("source_data", {}).get("type", "")),
                str(obj.get("success", "")),
            ])

            expected_signature = hmac.new(
                self.hmac_secret.encode(),
                concatenated.encode(),
                hashlib.sha512,
            ).hexdigest()

            if hmac.compare_digest(expected_signature, signature):
                return data
            else:
                logger.warning("Paymob webhook signature mismatch")
                return None

        except Exception as e:
            logger.error(f"Paymob webhook verification error: {e}")
            return None
