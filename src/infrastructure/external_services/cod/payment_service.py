"""Cash on Delivery (COD) payment service implementation.

COD is a popular payment method in Egypt and MENA where customers pay
cash to the courier upon delivery. This service manages COD payment
intents and collection status tracking.
"""

import uuid
from datetime import datetime

from src.config import settings
from src.core.exceptions import PaymentError
from src.core.interfaces.services.payment_service import (
    CODCollectionStatus,
    CODPaymentIntent,
    IPaymentService,
    PaymentIntent,
    PaymentProvider,
    PaymentResult,
    RefundResult,
)


class CODPaymentService(IPaymentService):
    """Cash on Delivery payment service implementation.

    Unlike card-based payment services, COD doesn't process payments
    immediately. Instead, it creates payment intents that track the
    collection status when the courier delivers the order.

    Flow:
    1. create_payment_intent() - Called when order is placed
    2. Order is shipped to customer
    3. Courier collects cash on delivery
    4. confirm_payment() - Called when courier confirms collection
    5. Or cancel_payment() - Called if delivery fails/refused
    """

    def __init__(self) -> None:
        self.enabled = settings.cod_enabled
        self.fee_percentage = settings.cod_fee_percentage
        self.fee_flat = settings.cod_fee_flat
        self.max_amount = settings.cod_max_amount
        self.min_amount = settings.cod_min_amount
        # In-memory storage for demo; in production use database
        self._intents: dict[str, CODPaymentIntent] = {}

    @property
    def provider(self) -> PaymentProvider:
        """Get the payment provider."""
        return PaymentProvider.COD

    def _calculate_cod_fee(self, amount: int) -> int:
        """Calculate COD fee based on settings.

        Args:
            amount: Order amount in cents

        Returns:
            COD fee in cents
        """
        percentage_fee = int(amount * (self.fee_percentage / 100))
        return percentage_fee + self.fee_flat

    def _validate_amount(self, amount: int) -> None:
        """Validate COD amount is within allowed range.

        Args:
            amount: Amount in cents

        Raises:
            PaymentError: If amount is outside allowed range
        """
        if not self.enabled:
            raise PaymentError("Cash on Delivery is not enabled for this store")

        if amount < self.min_amount:
            raise PaymentError(
                f"COD minimum amount is {self.min_amount / 100:.2f}. "
                f"Order amount {amount / 100:.2f} is below minimum."
            )

        if amount > self.max_amount:
            raise PaymentError(
                f"COD maximum amount is {self.max_amount / 100:.2f}. "
                f"Order amount {amount / 100:.2f} exceeds maximum."
            )

    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Create a COD payment intent.

        This creates a pending COD payment that will be collected
        upon delivery. The intent tracks the order and collection status.

        Args:
            amount: Amount to collect in cents
            currency: Currency code (e.g., "EGP")
            customer_email: Customer email (optional, for notifications)
            metadata: Additional metadata (should include order_id)

        Returns:
            PaymentIntent with COD-specific fields

        Raises:
            PaymentError: If COD is disabled or amount is invalid
        """
        self._validate_amount(amount)

        intent_id = f"cod_{uuid.uuid4().hex[:16]}"
        cod_fee = self._calculate_cod_fee(amount)
        order_id = (metadata or {}).get("order_id", "")

        cod_intent = CODPaymentIntent(
            id=intent_id,
            order_id=order_id,
            amount=amount,
            currency=currency.upper(),
            cod_fee=cod_fee,
            total_to_collect=amount + cod_fee,
            collection_status=CODCollectionStatus.PENDING,
            provider=PaymentProvider.COD,
            metadata=metadata or {},
        )

        # Store the intent
        self._intents[intent_id] = cod_intent

        # Return standard PaymentIntent for API compatibility
        return PaymentIntent(
            id=intent_id,
            client_secret="",  # COD doesn't need client secret
            amount=cod_intent.total_to_collect,
            currency=currency.upper(),
            status="pending_collection",
            provider=PaymentProvider.COD,
        )

    async def create_cod_intent(
        self,
        amount: int,
        currency: str,
        order_id: str,
        metadata: dict | None = None,
    ) -> CODPaymentIntent:
        """Create a COD-specific payment intent with full details.

        This is the preferred method for COD as it returns the full
        CODPaymentIntent with fee breakdown.

        Args:
            amount: Order amount in cents
            currency: Currency code (e.g., "EGP")
            order_id: Associated order ID
            metadata: Additional metadata

        Returns:
            CODPaymentIntent with fee details
        """
        self._validate_amount(amount)

        intent_id = f"cod_{uuid.uuid4().hex[:16]}"
        cod_fee = self._calculate_cod_fee(amount)

        cod_intent = CODPaymentIntent(
            id=intent_id,
            order_id=order_id,
            amount=amount,
            currency=currency.upper(),
            cod_fee=cod_fee,
            total_to_collect=amount + cod_fee,
            collection_status=CODCollectionStatus.PENDING,
            provider=PaymentProvider.COD,
            metadata=metadata or {},
        )

        self._intents[intent_id] = cod_intent
        return cod_intent

    def get_cod_intent(self, intent_id: str) -> CODPaymentIntent | None:
        """Get a COD payment intent by ID.

        Args:
            intent_id: The COD intent ID

        Returns:
            CODPaymentIntent or None if not found
        """
        return self._intents.get(intent_id)

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        """Confirm COD payment was collected.

        Called when the courier confirms cash collection from customer.

        Args:
            payment_intent_id: The COD intent ID

        Returns:
            PaymentResult indicating success/failure
        """
        intent = self._intents.get(payment_intent_id)
        if not intent:
            return PaymentResult(
                success=False,
                error_message=f"COD intent {payment_intent_id} not found",
                error_code="intent_not_found",
            )

        if intent.collection_status == CODCollectionStatus.COLLECTED:
            return PaymentResult(
                success=True,
                payment_id=payment_intent_id,
            )

        if intent.collection_status in (
            CODCollectionStatus.FAILED,
            CODCollectionStatus.RETURNED,
        ):
            return PaymentResult(
                success=False,
                payment_id=payment_intent_id,
                error_message=f"Cannot confirm: status is {intent.collection_status.value}",
                error_code="invalid_status",
            )

        # Update status to collected
        self._intents[payment_intent_id] = CODPaymentIntent(
            id=intent.id,
            order_id=intent.order_id,
            amount=intent.amount,
            currency=intent.currency,
            cod_fee=intent.cod_fee,
            total_to_collect=intent.total_to_collect,
            collection_status=CODCollectionStatus.COLLECTED,
            provider=intent.provider,
            metadata={**intent.metadata, "collected_at": datetime.utcnow().isoformat()},
        )

        return PaymentResult(
            success=True,
            payment_id=payment_intent_id,
        )

    async def mark_collection_failed(
        self, payment_intent_id: str, reason: str = ""
    ) -> PaymentResult:
        """Mark COD collection as failed.

        Called when delivery fails or customer refuses to pay.

        Args:
            payment_intent_id: The COD intent ID
            reason: Reason for failure

        Returns:
            PaymentResult indicating the update status
        """
        intent = self._intents.get(payment_intent_id)
        if not intent:
            return PaymentResult(
                success=False,
                error_message=f"COD intent {payment_intent_id} not found",
                error_code="intent_not_found",
            )

        self._intents[payment_intent_id] = CODPaymentIntent(
            id=intent.id,
            order_id=intent.order_id,
            amount=intent.amount,
            currency=intent.currency,
            cod_fee=intent.cod_fee,
            total_to_collect=intent.total_to_collect,
            collection_status=CODCollectionStatus.FAILED,
            provider=intent.provider,
            metadata={
                **intent.metadata,
                "failed_at": datetime.utcnow().isoformat(),
                "failure_reason": reason,
            },
        )

        return PaymentResult(
            success=True,
            payment_id=payment_intent_id,
        )

    async def mark_returned(self, payment_intent_id: str) -> PaymentResult:
        """Mark order as returned (COD not collected).

        Called when the order is returned to sender.

        Args:
            payment_intent_id: The COD intent ID

        Returns:
            PaymentResult indicating the update status
        """
        intent = self._intents.get(payment_intent_id)
        if not intent:
            return PaymentResult(
                success=False,
                error_message=f"COD intent {payment_intent_id} not found",
                error_code="intent_not_found",
            )

        self._intents[payment_intent_id] = CODPaymentIntent(
            id=intent.id,
            order_id=intent.order_id,
            amount=intent.amount,
            currency=intent.currency,
            cod_fee=intent.cod_fee,
            total_to_collect=intent.total_to_collect,
            collection_status=CODCollectionStatus.RETURNED,
            provider=intent.provider,
            metadata={**intent.metadata, "returned_at": datetime.utcnow().isoformat()},
        )

        return PaymentResult(
            success=True,
            payment_id=payment_intent_id,
        )

    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        """Capture payment - same as confirm for COD.

        Args:
            payment_intent_id: The COD intent ID

        Returns:
            PaymentResult
        """
        return await self.confirm_payment(payment_intent_id)

    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
        """Cancel a COD payment intent.

        Called when the order is cancelled before delivery.

        Args:
            payment_intent_id: The COD intent ID

        Returns:
            PaymentResult indicating success/failure
        """
        intent = self._intents.get(payment_intent_id)
        if not intent:
            return PaymentResult(
                success=False,
                error_message=f"COD intent {payment_intent_id} not found",
                error_code="intent_not_found",
            )

        if intent.collection_status == CODCollectionStatus.COLLECTED:
            return PaymentResult(
                success=False,
                payment_id=payment_intent_id,
                error_message="Cannot cancel: payment already collected",
                error_code="already_collected",
            )

        # Remove the intent
        del self._intents[payment_intent_id]

        return PaymentResult(
            success=True,
            payment_id=payment_intent_id,
        )

    async def refund_payment(
        self,
        payment_id: str,
        amount: int | None = None,
    ) -> RefundResult:
        """Refund a COD payment.

        For COD, refunds are handled manually (cash return to customer).
        This method just records the refund request.

        Args:
            payment_id: The COD intent ID
            amount: Amount to refund in cents (partial refund if specified)

        Returns:
            RefundResult indicating success
        """
        intent = self._intents.get(payment_id)
        if not intent:
            return RefundResult(
                success=False,
                error_message=f"COD intent {payment_id} not found",
            )

        if intent.collection_status != CODCollectionStatus.COLLECTED:
            return RefundResult(
                success=False,
                error_message="Cannot refund: payment not collected",
            )

        refund_amount = amount or intent.amount
        refund_id = f"cod_refund_{uuid.uuid4().hex[:12]}"

        # Update metadata with refund info
        self._intents[payment_id] = CODPaymentIntent(
            id=intent.id,
            order_id=intent.order_id,
            amount=intent.amount,
            currency=intent.currency,
            cod_fee=intent.cod_fee,
            total_to_collect=intent.total_to_collect,
            collection_status=intent.collection_status,
            provider=intent.provider,
            metadata={
                **intent.metadata,
                "refund_id": refund_id,
                "refund_amount": refund_amount,
                "refunded_at": datetime.utcnow().isoformat(),
            },
        )

        return RefundResult(
            success=True,
            refund_id=refund_id,
        )

    async def get_payment_status(self, payment_id: str) -> str:
        """Get COD payment status.

        Args:
            payment_id: The COD intent ID

        Returns:
            Status string

        Raises:
            PaymentError: If intent not found
        """
        intent = self._intents.get(payment_id)
        if not intent:
            raise PaymentError(f"COD intent {payment_id} not found")
        return intent.collection_status.value

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify webhook signature.

        COD doesn't have external webhooks since it's internal.
        This is here for interface compatibility.

        Returns:
            None (COD doesn't use webhooks)
        """
        return None
