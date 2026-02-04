"""Fawry payment service implementation for Egyptian market.

Fawry is Egypt's largest electronic payments network with 250,000+
retail locations. Customers can pay using:
- Fawry retail outlets (pharmacies, supermarkets, etc.)
- Fawry mobile app
- FawryPay online

API Documentation: https://developer.fawrystaging.com/
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from src.config import settings
from src.core.exceptions import PaymentError
from src.core.interfaces.services.payment_service import (
    FawryReferenceNumber,
    IPaymentService,
    PaymentIntent,
    PaymentProvider,
    PaymentResult,
    RefundResult,
)

logger = logging.getLogger(__name__)


class FawryPaymentService(IPaymentService):
    """Fawry payment service for Egyptian retail payments.

    Flow:
    1. create_reference_number() - Generate Fawry reference
    2. Customer receives reference (SMS/email/display)
    3. Customer pays at Fawry outlet or online
    4. Fawry webhook notifies payment
    5. verify_webhook_signature() - Validate notification
    6. Order is fulfilled

    Reference numbers expire after a configurable period (default 24h).
    """

    def __init__(
        self,
        merchant_code: str | None = None,
        security_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.merchant_code = merchant_code or settings.fawry_merchant_code
        self.security_key = security_key or settings.fawry_security_key
        self.base_url = base_url or settings.fawry_base_url
        # Default expiry in hours
        self.default_expiry_hours = 24

    @property
    def provider(self) -> PaymentProvider:
        """Get the payment provider."""
        return PaymentProvider.FAWRY

    def _generate_signature(self, *args: str) -> str:
        """Generate Fawry request signature.

        Fawry uses SHA-256 hash of concatenated values.

        Args:
            *args: Values to concatenate and hash

        Returns:
            SHA-256 hash string
        """
        concatenated = "".join(str(arg) for arg in args)
        return hashlib.sha256(concatenated.encode()).hexdigest()

    async def create_reference_number(
        self,
        amount: int,
        currency: str,
        merchant_ref_number: str,
        customer_email: str | None = None,
        customer_mobile: str | None = None,
        customer_name: str | None = None,
        description: str | None = None,
        expiry_hours: int | None = None,
        items: list[dict] | None = None,
    ) -> FawryReferenceNumber:
        """Create a Fawry payment reference number.

        Args:
            amount: Amount in cents
            currency: Currency code (EGP)
            merchant_ref_number: Your unique order reference
            customer_email: Customer email for notification
            customer_mobile: Customer mobile (+201xxxxxxxxx)
            customer_name: Customer name
            description: Payment description
            expiry_hours: Reference expiry in hours
            items: Order items for display

        Returns:
            FawryReferenceNumber with reference and expiry

        Raises:
            PaymentError: If reference creation fails
        """
        if not self.merchant_code or not self.security_key:
            raise PaymentError("Fawry credentials not configured")

        expiry_hours = expiry_hours or self.default_expiry_hours
        expiry_date = datetime.utcnow() + timedelta(hours=expiry_hours)
        expiry_timestamp = int(
            expiry_date.timestamp() * 1000
        )  # Fawry uses milliseconds

        # Build charge items
        charge_items = []
        if items:
            for item in items:
                charge_items.append({
                    "itemId": item.get("id", "item"),
                    "description": item.get("description", item.get("name", "Item")),
                    "price": item.get("price", amount / 100),
                    "quantity": item.get("quantity", 1),
                })
        else:
            # Default single item
            charge_items.append({
                "itemId": merchant_ref_number,
                "description": description or "Order payment",
                "price": amount / 100,  # Fawry uses actual currency units
                "quantity": 1,
            })

        # Generate signature
        # Signature = SHA256(merchantCode + merchantRefNum + customerProfileId + paymentMethod + amount + securityKey)
        signature = self._generate_signature(
            self.merchant_code,
            merchant_ref_number,
            customer_mobile or customer_email or "guest",
            "PAYATFAWRY",
            f"{amount / 100:.2f}",  # Amount in EGP
            self.security_key,
        )

        # Build request payload
        payload = {
            "merchantCode": self.merchant_code,
            "merchantRefNum": merchant_ref_number,
            "customerProfileId": customer_mobile or customer_email or "guest",
            "customerEmail": customer_email,
            "customerMobile": customer_mobile,
            "customerName": customer_name,
            "paymentMethod": "PAYATFAWRY",
            "paymentExpiry": expiry_timestamp,
            "chargeItems": charge_items,
            "signature": signature,
            "description": description,
        }

        # Remove None values
        payload = {k: v for k, v in payload.items() if v is not None}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/ECommerceWeb/Fawry/payments/charge",
                json=payload,
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Fawry reference creation failed: {response.text}")
                raise PaymentError("Failed to create Fawry reference number")

            data = response.json()

            if data.get("statusCode") != 200:
                error_msg = data.get("statusDescription", "Unknown error")
                logger.error(f"Fawry error: {error_msg}")
                raise PaymentError(f"Fawry error: {error_msg}")

            reference_number = data.get("referenceNumber")
            if not reference_number:
                raise PaymentError("No reference number in Fawry response")

            return FawryReferenceNumber(
                reference_number=reference_number,
                merchant_ref_number=merchant_ref_number,
                amount=amount,
                currency=currency.upper(),
                expiry_date=expiry_date,
                payment_status="NEW",
                provider=PaymentProvider.FAWRY,
                payment_url=f"https://atfawry.fawrystaging.com/atfawry/plugin/invoice?referenceNumber={reference_number}",
            )

    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Create a Fawry payment intent (reference number).

        This wraps create_reference_number for IPaymentService compatibility.

        Args:
            amount: Amount in cents
            currency: Currency code
            customer_email: Customer email
            metadata: Should include merchant_ref_number, customer_mobile

        Returns:
            PaymentIntent with reference number as ID
        """
        metadata = metadata or {}
        merchant_ref_number = metadata.get(
            "order_id", metadata.get("merchant_ref_number")
        )
        if not merchant_ref_number:
            import uuid

            merchant_ref_number = f"fawry_{uuid.uuid4().hex[:12]}"

        reference = await self.create_reference_number(
            amount=amount,
            currency=currency,
            merchant_ref_number=merchant_ref_number,
            customer_email=customer_email,
            customer_mobile=metadata.get("customer_mobile"),
            customer_name=metadata.get("customer_name"),
            description=metadata.get("description"),
            items=metadata.get("items"),
        )

        return PaymentIntent(
            id=reference.reference_number,
            client_secret=reference.merchant_ref_number,  # For reference
            amount=amount,
            currency=currency.upper(),
            status="pending",
            provider=PaymentProvider.FAWRY,
        )

    async def get_payment_status_details(
        self,
        merchant_ref_number: str,
    ) -> dict[str, Any]:
        """Get detailed Fawry payment status.

        Args:
            merchant_ref_number: Your order reference

        Returns:
            Full status details from Fawry
        """
        if not self.merchant_code or not self.security_key:
            raise PaymentError("Fawry credentials not configured")

        signature = self._generate_signature(
            self.merchant_code,
            merchant_ref_number,
            self.security_key,
        )

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/ECommerceWeb/Fawry/payments/status/v2",
                params={
                    "merchantCode": self.merchant_code,
                    "merchantRefNumber": merchant_ref_number,
                    "signature": signature,
                },
                timeout=30.0,
            )

            if response.status_code != 200:
                raise PaymentError("Failed to get Fawry payment status")

            return response.json()

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        """Check if Fawry payment is confirmed.

        Fawry payments are confirmed via webhook, this checks status.

        Args:
            payment_intent_id: Merchant reference number

        Returns:
            PaymentResult based on Fawry status
        """
        try:
            status_data = await self.get_payment_status_details(payment_intent_id)

            status = status_data.get("paymentStatus", "")
            is_paid = status == "PAID"

            return PaymentResult(
                success=is_paid,
                payment_id=status_data.get("referenceNumber"),
                error_message=None if is_paid else f"Status: {status}",
            )
        except PaymentError as e:
            return PaymentResult(
                success=False,
                error_message=str(e),
            )

    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        """Capture payment - Fawry payments are auto-captured.

        Args:
            payment_intent_id: Merchant reference number

        Returns:
            PaymentResult
        """
        return await self.confirm_payment(payment_intent_id)

    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
        """Cancel a Fawry payment reference.

        This cancels an unpaid reference number.

        Args:
            payment_intent_id: Merchant reference number

        Returns:
            PaymentResult
        """
        if not self.merchant_code or not self.security_key:
            return PaymentResult(
                success=False,
                error_message="Fawry credentials not configured",
            )

        signature = self._generate_signature(
            self.merchant_code,
            payment_intent_id,
            self.security_key,
        )

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{self.base_url}/ECommerceWeb/Fawry/payments",
                params={
                    "merchantCode": self.merchant_code,
                    "merchantRefNumber": payment_intent_id,
                    "signature": signature,
                },
                timeout=30.0,
            )

            if response.status_code not in (200, 204):
                logger.error(f"Fawry cancel failed: {response.text}")
                return PaymentResult(
                    success=False,
                    error_message="Failed to cancel Fawry reference",
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
        """Refund a Fawry payment.

        Args:
            payment_id: Fawry reference number
            amount: Amount to refund in cents (full if None)

        Returns:
            RefundResult
        """
        if not self.merchant_code or not self.security_key:
            return RefundResult(
                success=False,
                error_message="Fawry credentials not configured",
            )

        # Get original payment details first
        try:
            status_data = await self.get_payment_status_details(payment_id)
            if status_data.get("paymentStatus") != "PAID":
                return RefundResult(
                    success=False,
                    error_message="Cannot refund: payment not completed",
                )
        except PaymentError:
            pass  # Continue with refund attempt

        # If no amount specified, we need the original amount
        refund_amount = amount
        if not refund_amount:
            refund_amount = int(float(status_data.get("paymentAmount", 0)) * 100)

        signature = self._generate_signature(
            self.merchant_code,
            payment_id,
            f"{refund_amount / 100:.2f}",
            self.security_key,
        )

        payload = {
            "merchantCode": self.merchant_code,
            "referenceNumber": payment_id,
            "refundAmount": refund_amount / 100,
            "signature": signature,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/ECommerceWeb/Fawry/payments/refund",
                json=payload,
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Fawry refund failed: {response.text}")
                return RefundResult(
                    success=False,
                    error_message="Failed to refund payment",
                )

            data = response.json()
            if data.get("statusCode") != 200:
                return RefundResult(
                    success=False,
                    error_message=data.get("statusDescription", "Refund failed"),
                )

            return RefundResult(
                success=True,
                refund_id=data.get("referenceNumber"),
            )

    async def get_payment_status(self, payment_id: str) -> str:
        """Get Fawry payment status.

        Args:
            payment_id: Merchant reference number

        Returns:
            Status string (NEW, PAID, EXPIRED, CANCELED, REFUNDED)

        Raises:
            PaymentError: If status check fails
        """
        status_data = await self.get_payment_status_details(payment_id)
        return status_data.get("paymentStatus", "UNKNOWN")

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify Fawry webhook signature.

        Fawry webhook signature is SHA-256 of:
        referenceNumber + merchantRefNum + paymentAmount + orderAmount +
        orderStatus + paymentMethod + fawryFees + shippingFees + authNumber +
        customerMail + customerMobile + securityKey

        Args:
            payload: Webhook payload bytes
            signature: Signature from Fawry

        Returns:
            Parsed payload if valid, None if invalid
        """
        if not self.security_key:
            logger.warning("Fawry security key not configured")
            return None

        try:
            import json

            data = json.loads(payload)

            # Build signature string
            sig_parts = [
                str(data.get("referenceNumber", "")),
                str(data.get("merchantRefNum", "")),
                str(data.get("paymentAmount", "")),
                str(data.get("orderAmount", "")),
                str(data.get("orderStatus", "")),
                str(data.get("paymentMethod", "")),
                str(data.get("fawryFees", "") or ""),
                str(data.get("shippingFees", "") or ""),
                str(data.get("authNumber", "") or ""),
                str(data.get("customerMail", "") or ""),
                str(data.get("customerMobile", "") or ""),
                self.security_key,
            ]

            expected_signature = hashlib.sha256("".join(sig_parts).encode()).hexdigest()

            if expected_signature.lower() == signature.lower():
                return data
            else:
                logger.warning("Fawry webhook signature mismatch")
                return None

        except Exception as e:
            logger.error(f"Fawry webhook verification error: {e}")
            return None

    def get_payment_url(self, reference_number: str) -> str:
        """Get the Fawry online payment URL.

        Args:
            reference_number: Fawry reference number

        Returns:
            URL for online payment
        """
        # Use production URL in production environment
        base = (
            self.base_url.replace("fawrystaging", "fawry")
            if settings.environment == "production"
            else self.base_url
        )
        return f"{base.replace('/api', '')}/atfawry/plugin/invoice?referenceNumber={reference_number}"
