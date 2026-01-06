"""Payment service interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class PaymentProvider(str, Enum):
    """Payment provider enumeration."""

    STRIPE = "stripe"
    TAP = "tap"


@dataclass
class PaymentIntent:
    """Payment intent data."""

    id: str
    client_secret: str
    amount: int  # In cents
    currency: str
    status: str
    provider: PaymentProvider


@dataclass
class PaymentResult:
    """Payment result data."""

    success: bool
    payment_id: str | None = None
    error_message: str | None = None
    error_code: str | None = None


@dataclass
class RefundResult:
    """Refund result data."""

    success: bool
    refund_id: str | None = None
    error_message: str | None = None


class IPaymentService(ABC):
    """Payment service interface."""

    @property
    @abstractmethod
    def provider(self) -> PaymentProvider:
        """Get the payment provider."""
        ...

    @abstractmethod
    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Create a payment intent."""
        ...

    @abstractmethod
    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        """Confirm a payment."""
        ...

    @abstractmethod
    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        """Capture an authorized payment."""
        ...

    @abstractmethod
    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
        """Cancel a payment intent."""
        ...

    @abstractmethod
    async def refund_payment(
        self,
        payment_id: str,
        amount: int | None = None,  # Partial refund if specified
    ) -> RefundResult:
        """Refund a payment."""
        ...

    @abstractmethod
    async def get_payment_status(self, payment_id: str) -> str:
        """Get payment status."""
        ...

    @abstractmethod
    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify webhook signature and return event data."""
        ...
