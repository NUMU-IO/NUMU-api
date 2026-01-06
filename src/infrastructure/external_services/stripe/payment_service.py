"""Stripe payment service implementation."""

import stripe

from src.config import settings
from src.core.exceptions import PaymentError
from src.core.interfaces.services.payment_service import (
    IPaymentService,
    PaymentIntent,
    PaymentProvider,
    PaymentResult,
    RefundResult,
)


class StripePaymentService(IPaymentService):
    """Stripe payment service implementation."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.stripe_secret_key
        if self.api_key:
            stripe.api_key = self.api_key

    @property
    def provider(self) -> PaymentProvider:
        """Get the payment provider."""
        return PaymentProvider.STRIPE

    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Create a Stripe payment intent."""
        try:
            intent = stripe.PaymentIntent.create(
                amount=amount,
                currency=currency.lower(),
                receipt_email=customer_email,
                metadata=metadata or {},
            )
            return PaymentIntent(
                id=intent.id,
                client_secret=intent.client_secret,
                amount=intent.amount,
                currency=intent.currency.upper(),
                status=intent.status,
                provider=PaymentProvider.STRIPE,
            )
        except stripe.StripeError as e:
            raise PaymentError(str(e))

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        """Confirm a Stripe payment."""
        try:
            intent = stripe.PaymentIntent.confirm(payment_intent_id)
            return PaymentResult(
                success=intent.status == "succeeded",
                payment_id=intent.id,
            )
        except stripe.StripeError as e:
            return PaymentResult(
                success=False,
                error_message=str(e),
                error_code=e.code if hasattr(e, "code") else None,
            )

    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        """Capture an authorized Stripe payment."""
        try:
            intent = stripe.PaymentIntent.capture(payment_intent_id)
            return PaymentResult(
                success=intent.status == "succeeded",
                payment_id=intent.id,
            )
        except stripe.StripeError as e:
            return PaymentResult(
                success=False,
                error_message=str(e),
            )

    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
        """Cancel a Stripe payment intent."""
        try:
            intent = stripe.PaymentIntent.cancel(payment_intent_id)
            return PaymentResult(
                success=intent.status == "canceled",
                payment_id=intent.id,
            )
        except stripe.StripeError as e:
            return PaymentResult(
                success=False,
                error_message=str(e),
            )

    async def refund_payment(
        self,
        payment_id: str,
        amount: int | None = None,
    ) -> RefundResult:
        """Refund a Stripe payment."""
        try:
            refund_params = {"payment_intent": payment_id}
            if amount:
                refund_params["amount"] = amount
            refund = stripe.Refund.create(**refund_params)
            return RefundResult(
                success=refund.status == "succeeded",
                refund_id=refund.id,
            )
        except stripe.StripeError as e:
            return RefundResult(
                success=False,
                error_message=str(e),
            )

    async def get_payment_status(self, payment_id: str) -> str:
        """Get Stripe payment status."""
        try:
            intent = stripe.PaymentIntent.retrieve(payment_id)
            return intent.status
        except stripe.StripeError as e:
            raise PaymentError(str(e))

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify Stripe webhook signature and return event data."""
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                settings.stripe_webhook_secret,
            )
            return event
        except stripe.SignatureVerificationError:
            return None
