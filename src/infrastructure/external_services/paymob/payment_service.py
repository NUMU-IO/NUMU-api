"""Paymob payment service implementation for Egyptian market.

Paymob is Egypt's leading payment gateway supporting:
- Card payments (Visa, Mastercard, Meeza)
- Mobile wallets (Vodafone Cash, Orange Cash, Etisalat Cash, WE Pay)

API Documentation: https://docs.paymob.com/
"""

import hashlib
import hmac
import logging
from typing import Any

import httpx

from src.config import settings
from src.core.exceptions import PaymentError
from src.core.interfaces.services.payment_service import (
    IPaymentService,
    PaymentIntent,
    PaymentMethod,
    PaymentProvider,
    PaymentResult,
    PaymobPaymentKey,
    RefundResult,
)

logger = logging.getLogger(__name__)

# Paymob API base URL
PAYMOB_API_BASE = "https://accept.paymob.com/api"


class PaymobPaymentService(IPaymentService):
    """Paymob payment service for Egyptian cards and wallets.

    Flow:
    1. authenticate() - Get auth token
    2. create_order() - Register order with Paymob
    3. create_payment_key() - Get payment key for iframe/SDK
    4. Customer completes payment
    5. Webhook notification received
    6. verify_webhook_signature() - Validate and process
    """

    def __init__(
        self,
        api_key: str | None = None,
        integration_id: str | None = None,
        iframe_id: str | None = None,
        hmac_secret: str | None = None,
        wallet_integration_id: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.paymob_api_key
        self.integration_id = integration_id or settings.paymob_integration_id
        self.iframe_id = iframe_id or settings.paymob_iframe_id
        self.hmac_secret = hmac_secret or settings.paymob_hmac_secret
        self.wallet_integration_id = wallet_integration_id or settings.paymob_wallet_integration_id
        self._auth_token: str | None = None

    @property
    def provider(self) -> PaymentProvider:
        """Get the payment provider."""
        return PaymentProvider.PAYMOB

    async def _get_auth_token(self) -> str:
        """Get Paymob authentication token.

        Returns:
            Authentication token string

        Raises:
            PaymentError: If authentication fails
        """
        if self._auth_token:
            return self._auth_token

        if not self.api_key:
            raise PaymentError("Paymob API key not configured")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYMOB_API_BASE}/auth/tokens",
                json={"api_key": self.api_key},
                timeout=30.0,
            )

            if response.status_code != 201:
                logger.error(f"Paymob auth failed: {response.text}")
                raise PaymentError("Failed to authenticate with Paymob")

            data = response.json()
            self._auth_token = data.get("token")
            return self._auth_token

    async def _create_paymob_order(
        self,
        auth_token: str,
        amount: int,
        currency: str,
        merchant_order_id: str | None = None,
        items: list[dict] | None = None,
    ) -> str:
        """Register an order with Paymob.

        Args:
            auth_token: Paymob auth token
            amount: Amount in cents
            currency: Currency code (EGP)
            merchant_order_id: Your order ID
            items: Order items for display

        Returns:
            Paymob order ID

        Raises:
            PaymentError: If order creation fails
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYMOB_API_BASE}/ecommerce/orders",
                json={
                    "auth_token": auth_token,
                    "delivery_needed": "false",
                    "amount_cents": amount,
                    "currency": currency.upper(),
                    "merchant_order_id": merchant_order_id,
                    "items": items or [],
                },
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Paymob order creation failed: {response.text}")
                raise PaymentError("Failed to create Paymob order")

            data = response.json()
            return str(data.get("id"))

    async def _create_payment_key(
        self,
        auth_token: str,
        order_id: str,
        amount: int,
        currency: str,
        integration_id: str,
        billing_data: dict | None = None,
        expiration: int = 3600,
    ) -> str:
        """Create a payment key for the iframe/SDK.

        Args:
            auth_token: Paymob auth token
            order_id: Paymob order ID
            amount: Amount in cents
            currency: Currency code
            integration_id: Payment integration ID
            billing_data: Customer billing information
            expiration: Key expiration in seconds

        Returns:
            Payment key token

        Raises:
            PaymentError: If key creation fails
        """
        # Default billing data if not provided
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

        billing = {**default_billing, **(billing_data or {})}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYMOB_API_BASE}/acceptance/payment_keys",
                json={
                    "auth_token": auth_token,
                    "amount_cents": amount,
                    "expiration": expiration,
                    "order_id": order_id,
                    "billing_data": billing,
                    "currency": currency.upper(),
                    "integration_id": int(integration_id),
                },
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Paymob payment key creation failed: {response.text}")
                raise PaymentError("Failed to create Paymob payment key")

            data = response.json()
            return data.get("token")

    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Create a Paymob payment intent (order + payment key).

        Args:
            amount: Amount in cents
            currency: Currency code (EGP recommended)
            customer_email: Customer email
            metadata: Additional metadata (should include billing_data)

        Returns:
            PaymentIntent with Paymob payment key as client_secret
        """
        metadata = metadata or {}
        auth_token = await self._get_auth_token()

        # Create order
        merchant_order_id = metadata.get("order_id")
        items = metadata.get("items", [])
        paymob_order_id = await self._create_paymob_order(
            auth_token=auth_token,
            amount=amount,
            currency=currency,
            merchant_order_id=merchant_order_id,
            items=items,
        )

        # Prepare billing data
        billing_data = metadata.get("billing_data", {})
        if customer_email:
            billing_data["email"] = customer_email

        # Create payment key
        integration_id = self.integration_id
        if not integration_id:
            raise PaymentError("Paymob integration ID not configured")

        payment_key = await self._create_payment_key(
            auth_token=auth_token,
            order_id=paymob_order_id,
            amount=amount,
            currency=currency,
            integration_id=integration_id,
            billing_data=billing_data,
        )

        return PaymentIntent(
            id=paymob_order_id,
            client_secret=payment_key,  # Used for iframe
            amount=amount,
            currency=currency.upper(),
            status="pending",
            provider=PaymentProvider.PAYMOB,
        )

    async def create_card_payment(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        billing_data: dict | None = None,
        order_id: str | None = None,
        items: list[dict] | None = None,
    ) -> PaymobPaymentKey:
        """Create a card payment with full Paymob details.

        This returns the PaymobPaymentKey with iframe URL for embedding.

        Args:
            amount: Amount in cents
            currency: Currency code
            customer_email: Customer email
            billing_data: Billing information
            order_id: Your order ID
            items: Order items

        Returns:
            PaymobPaymentKey with iframe details
        """
        auth_token = await self._get_auth_token()

        # Create order
        paymob_order_id = await self._create_paymob_order(
            auth_token=auth_token,
            amount=amount,
            currency=currency,
            merchant_order_id=order_id,
            items=items,
        )

        # Prepare billing
        billing = billing_data or {}
        if customer_email:
            billing["email"] = customer_email

        if not self.integration_id:
            raise PaymentError("Paymob card integration ID not configured")

        payment_key = await self._create_payment_key(
            auth_token=auth_token,
            order_id=paymob_order_id,
            amount=amount,
            currency=currency,
            integration_id=self.integration_id,
            billing_data=billing,
        )

        return PaymobPaymentKey(
            payment_key=payment_key,
            order_id=paymob_order_id,
            iframe_id=self.iframe_id,
            amount=amount,
            currency=currency.upper(),
            expiry_seconds=3600,
            provider=PaymentProvider.PAYMOB,
        )

    async def create_wallet_payment(
        self,
        amount: int,
        currency: str,
        mobile_number: str,
        customer_email: str | None = None,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a mobile wallet payment (Vodafone Cash, etc.).

        Args:
            amount: Amount in cents
            currency: Currency code
            mobile_number: Customer mobile number (e.g., +201xxxxxxxxx)
            customer_email: Customer email
            order_id: Your order ID

        Returns:
            Dict with redirect_url for wallet app
        """
        if not self.wallet_integration_id:
            raise PaymentError("Paymob wallet integration ID not configured")

        auth_token = await self._get_auth_token()

        # Create order
        paymob_order_id = await self._create_paymob_order(
            auth_token=auth_token,
            amount=amount,
            currency=currency,
            merchant_order_id=order_id,
        )

        # Create payment key for wallet
        billing_data = {
            "phone_number": mobile_number,
            "email": customer_email or "customer@example.com",
        }

        payment_key = await self._create_payment_key(
            auth_token=auth_token,
            order_id=paymob_order_id,
            amount=amount,
            currency=currency,
            integration_id=self.wallet_integration_id,
            billing_data=billing_data,
        )

        # Request wallet payment
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYMOB_API_BASE}/acceptance/payments/pay",
                json={
                    "source": {
                        "identifier": mobile_number,
                        "subtype": "WALLET",
                    },
                    "payment_token": payment_key,
                },
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Paymob wallet payment failed: {response.text}")
                raise PaymentError("Failed to initiate wallet payment")

            data = response.json()
            return {
                "order_id": paymob_order_id,
                "redirect_url": data.get("redirect_url"),
                "iframe_redirection_url": data.get("iframe_redirection_url"),
            }

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        """Confirm a Paymob payment.

        Paymob payments are confirmed via webhooks, not API calls.
        This method checks the transaction status.

        Args:
            payment_intent_id: Paymob order ID

        Returns:
            PaymentResult based on transaction status
        """
        auth_token = await self._get_auth_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{PAYMOB_API_BASE}/ecommerce/orders/{payment_intent_id}",
                params={"auth_token": auth_token},
                timeout=30.0,
            )

            if response.status_code != 200:
                return PaymentResult(
                    success=False,
                    error_message="Failed to fetch order status",
                )

            data = response.json()
            is_paid = data.get("paid_amount_cents", 0) >= data.get("amount_cents", 0)

            return PaymentResult(
                success=is_paid,
                payment_id=payment_intent_id,
                error_message=None if is_paid else "Payment not completed",
            )

    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        """Capture payment - Paymob auto-captures by default.

        Args:
            payment_intent_id: Paymob order ID

        Returns:
            PaymentResult
        """
        # Paymob typically auto-captures
        return await self.confirm_payment(payment_intent_id)

    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
        """Cancel/void a Paymob payment.

        Args:
            payment_intent_id: Paymob order ID

        Returns:
            PaymentResult
        """
        auth_token = await self._get_auth_token()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYMOB_API_BASE}/acceptance/void_refund/void",
                params={"token": auth_token},
                json={"transaction_id": payment_intent_id},
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Paymob void failed: {response.text}")
                return PaymentResult(
                    success=False,
                    error_message="Failed to void payment",
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
        """Refund a Paymob payment.

        Args:
            payment_id: Paymob transaction ID
            amount: Amount to refund in cents (full refund if None)

        Returns:
            RefundResult
        """
        auth_token = await self._get_auth_token()

        refund_data = {"transaction_id": payment_id}
        if amount:
            refund_data["amount_cents"] = amount

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYMOB_API_BASE}/acceptance/void_refund/refund",
                params={"token": auth_token},
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
            return RefundResult(
                success=True,
                refund_id=str(data.get("id")),
            )

    async def get_payment_status(self, payment_id: str) -> str:
        """Get Paymob payment status.

        Args:
            payment_id: Paymob order ID

        Returns:
            Status string

        Raises:
            PaymentError: If status check fails
        """
        auth_token = await self._get_auth_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{PAYMOB_API_BASE}/ecommerce/orders/{payment_id}",
                params={"auth_token": auth_token},
                timeout=30.0,
            )

            if response.status_code != 200:
                raise PaymentError("Failed to get payment status")

            data = response.json()
            if data.get("paid_amount_cents", 0) >= data.get("amount_cents", 0):
                return "paid"
            elif data.get("is_cancel"):
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

        Args:
            payload: Webhook payload bytes
            signature: HMAC signature from header

        Returns:
            Parsed payload dict if valid, None if invalid
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

    def get_iframe_url(self, payment_key: str) -> str:
        """Get the iframe URL for card payment.

        Args:
            payment_key: Payment key from create_card_payment

        Returns:
            Iframe URL for embedding
        """
        if not self.iframe_id:
            raise PaymentError("Paymob iframe ID not configured")
        return f"https://accept.paymob.com/api/acceptance/iframes/{self.iframe_id}?payment_token={payment_key}"
