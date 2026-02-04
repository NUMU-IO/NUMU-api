"""Unit tests for Fawry payment service."""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.interfaces.services.payment_service import PaymentProvider
from src.infrastructure.external_services.fawry.payment_service import (
    FawryPaymentService,
)


class TestFawryPaymentService:
    """Tests for Fawry payment service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = FawryPaymentService(
            merchant_code="test_merchant",
            security_key="test_security_key",
            base_url="https://atfawry.fawrystaging.com",
        )

    def test_provider_is_fawry(self):
        """Test provider property returns FAWRY."""
        assert self.service.provider == PaymentProvider.FAWRY

    def test_generate_signature(self):
        """Test signature generation."""
        # Test internal signature generation
        sig = self.service._generate_signature("val1", "val2", "val3")
        expected = hashlib.sha256(b"val1val2val3").hexdigest()
        assert sig == expected

    @pytest.mark.asyncio
    async def test_create_payment_intent(self):
        """Test creating a Fawry payment intent."""
        with patch.object(self.service, "create_reference_number", new_callable=AsyncMock) as mock_create:
            from datetime import datetime, timedelta

            from src.core.interfaces.services.payment_service import (
                FawryReferenceNumber,
            )

            mock_create.return_value = FawryReferenceNumber(
                reference_number="123456789",
                merchant_ref_number="order-123",
                amount=10000,
                currency="EGP",
                expiry_date=datetime.utcnow() + timedelta(hours=24),
                payment_status="NEW",
                provider=PaymentProvider.FAWRY,
                payment_url="https://fawry.com/pay/123456789",
            )

            intent = await self.service.create_payment_intent(
                amount=10000,
                currency="EGP",
                customer_email="test@example.com",
                metadata={"order_id": "order-123"},
            )

            assert intent.id == "123456789"
            assert intent.amount == 10000
            assert intent.currency == "EGP"
            assert intent.status == "pending"
            assert intent.provider == PaymentProvider.FAWRY

    @pytest.mark.asyncio
    async def test_create_reference_number(self):
        """Test creating a Fawry reference number."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "statusCode": 200,
                "referenceNumber": "987654321",
            }
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            reference = await self.service.create_reference_number(
                amount=50000,
                currency="EGP",
                merchant_ref_number="order-456",
                customer_email="customer@example.com",
                customer_mobile="+201234567890",
            )

            assert reference.reference_number == "987654321"
            assert reference.merchant_ref_number == "order-456"
            assert reference.amount == 50000

    @pytest.mark.asyncio
    async def test_create_reference_number_no_credentials(self):
        """Test creating reference without credentials fails."""
        service = FawryPaymentService(
            merchant_code=None,
            security_key=None,
        )

        with pytest.raises(Exception) as exc_info:
            await service.create_reference_number(
                amount=10000,
                currency="EGP",
                merchant_ref_number="order-123",
            )

        assert "not configured" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_confirm_payment_paid(self):
        """Test confirming a paid Fawry payment."""
        with patch.object(self.service, "get_payment_status_details", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {
                "paymentStatus": "PAID",
                "referenceNumber": "123456789",
            }

            result = await self.service.confirm_payment("order-123")

            assert result.success is True
            assert result.payment_id == "123456789"

    @pytest.mark.asyncio
    async def test_confirm_payment_not_paid(self):
        """Test confirming unpaid Fawry payment."""
        with patch.object(self.service, "get_payment_status_details", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {
                "paymentStatus": "NEW",
                "referenceNumber": "123456789",
            }

            result = await self.service.confirm_payment("order-123")

            assert result.success is False

    def test_verify_webhook_signature_valid(self):
        """Test verifying valid Fawry webhook signature."""
        import json

        payload_data = {
            "referenceNumber": "123456789",
            "merchantRefNum": "order-123",
            "paymentAmount": "100.00",
            "orderAmount": "100.00",
            "orderStatus": "PAID",
            "paymentMethod": "PAYATFAWRY",
            "fawryFees": "",
            "shippingFees": "",
            "authNumber": "",
            "customerMail": "test@example.com",
            "customerMobile": "+201234567890",
        }
        payload = json.dumps(payload_data).encode()

        # Calculate expected signature
        sig_parts = [
            "123456789",
            "order-123",
            "100.00",
            "100.00",
            "PAID",
            "PAYATFAWRY",
            "",
            "",
            "",
            "test@example.com",
            "+201234567890",
            "test_security_key",
        ]
        expected_sig = hashlib.sha256("".join(sig_parts).encode()).hexdigest()

        result = self.service.verify_webhook_signature(payload, expected_sig)
        assert result is not None
        assert result["referenceNumber"] == "123456789"

    def test_verify_webhook_signature_invalid(self):
        """Test verifying invalid Fawry webhook signature."""
        payload = b'{"referenceNumber": "123456789"}'
        result = self.service.verify_webhook_signature(payload, "invalid_signature")
        assert result is None

    def test_verify_webhook_signature_no_secret(self):
        """Test webhook verification without secret."""
        service = FawryPaymentService(
            merchant_code="test",
            security_key=None,
        )
        result = service.verify_webhook_signature(b'{}', "sig")
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_payment(self):
        """Test cancelling a Fawry payment reference."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__aenter__.return_value.delete = AsyncMock(return_value=mock_response)

            result = await self.service.cancel_payment("order-123")

            assert result.success is True

    @pytest.mark.asyncio
    async def test_refund_payment(self):
        """Test refunding a Fawry payment."""
        with patch.object(self.service, "get_payment_status_details", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {
                "paymentStatus": "PAID",
                "paymentAmount": "100.00",
            }

            with patch("httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "statusCode": 200,
                    "referenceNumber": "refund_123",
                }
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

                result = await self.service.refund_payment("123456789")

                assert result.success is True

    @pytest.mark.asyncio
    async def test_get_payment_status(self):
        """Test getting payment status."""
        with patch.object(self.service, "get_payment_status_details", new_callable=AsyncMock) as mock_details:
            mock_details.return_value = {"paymentStatus": "PAID"}

            status = await self.service.get_payment_status("order-123")
            assert status == "PAID"

    def test_get_payment_url(self):
        """Test getting Fawry payment URL."""
        url = self.service.get_payment_url("123456789")
        assert "123456789" in url
        assert "fawry" in url.lower()
