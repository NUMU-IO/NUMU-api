"""Payment service interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class PaymentProvider(StrEnum):
    """Payment provider enumeration."""

    STRIPE = "stripe"
    TAP = "tap"
    # Egyptian payment methods
    COD = "cod"  # Cash on Delivery
    PAYMOB = "paymob"  # Paymob gateway (cards, wallets)
    FAWRY = "fawry"  # Fawry retail pay points
    KASHIER = "kashier"  # Kashier payment gateway
    FAWATERAK = "fawaterak"  # Fawaterak payment gateway
    INSTAPAY = "instapay"  # InstaPay (manual IPA + proof upload)


class PaymentMethod(StrEnum):
    """Payment method types."""

    CARD = "card"
    WALLET = "wallet"  # Mobile wallets (Vodafone Cash, etc.)
    BANK_TRANSFER = "bank_transfer"
    COD = "cod"  # Cash on Delivery
    FAWRY_REFERENCE = "fawry_reference"  # Pay at Fawry outlet


class CODCollectionStatus(StrEnum):
    """COD collection status."""

    PENDING = "pending"  # Awaiting delivery
    COLLECTED = "collected"  # Cash collected by courier
    FAILED = "failed"  # Collection failed
    RETURNED = "returned"  # Order returned


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


@dataclass
class CODPaymentIntent:
    """COD-specific payment intent."""

    id: str
    order_id: str
    amount: int  # In cents
    currency: str
    cod_fee: int  # COD fee in cents
    total_to_collect: int  # Amount + COD fee
    collection_status: CODCollectionStatus
    provider: PaymentProvider = PaymentProvider.COD
    metadata: dict = field(default_factory=dict)


@dataclass
class FawryReferenceNumber:
    """Fawry reference number for retail payment."""

    reference_number: str
    merchant_ref_number: str
    amount: int  # In cents
    currency: str
    expiry_date: datetime
    payment_status: str  # NEW, PAID, EXPIRED, CANCELED
    provider: PaymentProvider = PaymentProvider.FAWRY
    payment_url: str | None = None  # URL for online Fawry payment


@dataclass
class PaymobPaymentKey:
    """Paymob payment key for iframe/mobile SDK."""

    payment_key: str
    order_id: str
    iframe_id: str | None
    amount: int
    currency: str
    expiry_seconds: int = 3600
    provider: PaymentProvider = PaymentProvider.PAYMOB


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
