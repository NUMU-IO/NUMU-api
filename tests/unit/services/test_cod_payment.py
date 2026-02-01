"""Unit tests for COD (Cash on Delivery) payment service."""

import pytest

from src.core.interfaces.services.payment_service import (
    CODCollectionStatus,
    PaymentProvider,
)
from src.infrastructure.external_services.cod.payment_service import CODPaymentService


class TestCODPaymentService:
    """Tests for COD payment service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = CODPaymentService()
        self.service.enabled = True
        self.service.min_amount = 0
        self.service.max_amount = 1000000  # 10,000 EGP
        self.service.fee_percentage = 0
        self.service.fee_flat = 0

    @pytest.mark.asyncio
    async def test_provider_is_cod(self):
        """Test provider property returns COD."""
        assert self.service.provider == PaymentProvider.COD

    @pytest.mark.asyncio
    async def test_create_payment_intent(self):
        """Test creating a COD payment intent."""
        intent = await self.service.create_payment_intent(
            amount=50000,  # 500 EGP
            currency="EGP",
            customer_email="test@example.com",
            metadata={"order_id": "order-123"},
        )

        assert intent.id.startswith("cod_")
        assert intent.amount == 50000
        assert intent.currency == "EGP"
        assert intent.status == "pending_collection"
        assert intent.provider == PaymentProvider.COD

    @pytest.mark.asyncio
    async def test_create_cod_intent_with_fee(self):
        """Test creating COD intent with fee calculation."""
        self.service.fee_percentage = 5  # 5%
        self.service.fee_flat = 500  # 5 EGP

        intent = await self.service.create_cod_intent(
            amount=10000,  # 100 EGP
            currency="EGP",
            order_id="order-123",
        )

        assert intent.amount == 10000
        assert intent.cod_fee == 1000  # 5% of 100 + 5 flat = 10 EGP
        assert intent.total_to_collect == 11000  # 110 EGP
        assert intent.collection_status == CODCollectionStatus.PENDING

    @pytest.mark.asyncio
    async def test_create_cod_intent_no_fee(self):
        """Test creating COD intent without fee."""
        self.service.fee_percentage = 0
        self.service.fee_flat = 0

        intent = await self.service.create_cod_intent(
            amount=10000,
            currency="EGP",
            order_id="order-123",
        )

        assert intent.cod_fee == 0
        assert intent.total_to_collect == 10000

    @pytest.mark.asyncio
    async def test_confirm_payment(self):
        """Test confirming COD payment (cash collected)."""
        # Create intent first
        intent = await self.service.create_cod_intent(
            amount=10000,
            currency="EGP",
            order_id="order-123",
        )

        # Confirm payment
        result = await self.service.confirm_payment(intent.id)

        assert result.success is True
        assert result.payment_id == intent.id

        # Check status updated
        updated = self.service.get_cod_intent(intent.id)
        assert updated.collection_status == CODCollectionStatus.COLLECTED

    @pytest.mark.asyncio
    async def test_confirm_payment_not_found(self):
        """Test confirming non-existent payment."""
        result = await self.service.confirm_payment("non-existent-id")

        assert result.success is False
        assert result.error_code == "intent_not_found"

    @pytest.mark.asyncio
    async def test_mark_collection_failed(self):
        """Test marking COD collection as failed."""
        intent = await self.service.create_cod_intent(
            amount=10000,
            currency="EGP",
            order_id="order-123",
        )

        result = await self.service.mark_collection_failed(
            intent.id, reason="Customer refused"
        )

        assert result.success is True

        updated = self.service.get_cod_intent(intent.id)
        assert updated.collection_status == CODCollectionStatus.FAILED
        assert "Customer refused" in updated.metadata.get("failure_reason", "")

    @pytest.mark.asyncio
    async def test_mark_returned(self):
        """Test marking order as returned."""
        intent = await self.service.create_cod_intent(
            amount=10000,
            currency="EGP",
            order_id="order-123",
        )

        result = await self.service.mark_returned(intent.id)

        assert result.success is True

        updated = self.service.get_cod_intent(intent.id)
        assert updated.collection_status == CODCollectionStatus.RETURNED

    @pytest.mark.asyncio
    async def test_cancel_payment(self):
        """Test cancelling COD payment."""
        intent = await self.service.create_cod_intent(
            amount=10000,
            currency="EGP",
            order_id="order-123",
        )

        result = await self.service.cancel_payment(intent.id)

        assert result.success is True
        assert self.service.get_cod_intent(intent.id) is None

    @pytest.mark.asyncio
    async def test_cancel_collected_payment_fails(self):
        """Test cancelling already collected payment fails."""
        intent = await self.service.create_cod_intent(
            amount=10000,
            currency="EGP",
            order_id="order-123",
        )

        # Confirm collection first
        await self.service.confirm_payment(intent.id)

        # Try to cancel
        result = await self.service.cancel_payment(intent.id)

        assert result.success is False
        assert result.error_code == "already_collected"

    @pytest.mark.asyncio
    async def test_refund_payment(self):
        """Test refunding COD payment."""
        intent = await self.service.create_cod_intent(
            amount=10000,
            currency="EGP",
            order_id="order-123",
        )

        # Confirm collection first
        await self.service.confirm_payment(intent.id)

        # Request refund
        result = await self.service.refund_payment(intent.id)

        assert result.success is True
        assert result.refund_id.startswith("cod_refund_")

    @pytest.mark.asyncio
    async def test_refund_uncollected_payment_fails(self):
        """Test refunding uncollected payment fails."""
        intent = await self.service.create_cod_intent(
            amount=10000,
            currency="EGP",
            order_id="order-123",
        )

        result = await self.service.refund_payment(intent.id)

        assert result.success is False
        assert "not collected" in result.error_message

    @pytest.mark.asyncio
    async def test_get_payment_status(self):
        """Test getting payment status."""
        intent = await self.service.create_cod_intent(
            amount=10000,
            currency="EGP",
            order_id="order-123",
        )

        status = await self.service.get_payment_status(intent.id)
        assert status == "pending"

        await self.service.confirm_payment(intent.id)

        status = await self.service.get_payment_status(intent.id)
        assert status == "collected"

    @pytest.mark.asyncio
    async def test_amount_validation_min(self):
        """Test minimum amount validation."""
        self.service.min_amount = 5000  # 50 EGP minimum

        with pytest.raises(Exception) as exc_info:
            await self.service.create_payment_intent(
                amount=1000,  # 10 EGP - below minimum
                currency="EGP",
            )

        assert "minimum" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_amount_validation_max(self):
        """Test maximum amount validation."""
        self.service.max_amount = 100000  # 1000 EGP maximum

        with pytest.raises(Exception) as exc_info:
            await self.service.create_payment_intent(
                amount=200000,  # 2000 EGP - above maximum
                currency="EGP",
            )

        assert "maximum" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_disabled_service(self):
        """Test COD disabled raises error."""
        self.service.enabled = False

        with pytest.raises(Exception) as exc_info:
            await self.service.create_payment_intent(
                amount=10000,
                currency="EGP",
            )

        assert "not enabled" in str(exc_info.value).lower()

    def test_verify_webhook_returns_none(self):
        """Test webhook verification returns None (COD has no webhooks)."""
        result = self.service.verify_webhook_signature(b"payload", "signature")
        assert result is None
