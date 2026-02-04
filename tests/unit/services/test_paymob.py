"""Unit tests for Paymob payment service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.interfaces.services.payment_service import PaymentProvider
from src.infrastructure.external_services.paymob.payment_service import (
    PaymobPaymentService,
)


class TestPaymobPaymentService:
    """Tests for Paymob payment service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = PaymobPaymentService(
            api_key="test_api_key",
            integration_id="123456",
            iframe_id="789012",
            hmac_secret="test_hmac_secret",
        )

    def test_provider_is_paymob(self):
        """Test provider property returns PAYMOB."""
        assert self.service.provider == PaymentProvider.PAYMOB

    def test_get_iframe_url(self):
        """Test getting iframe URL."""
        url = self.service.get_iframe_url("payment_key_123")
        assert "789012" in url  # iframe_id
        assert "payment_key_123" in url
        assert "accept.paymob.com" in url

    def test_get_iframe_url_no_iframe_id(self):
        """Test iframe URL raises when no iframe ID."""
        service = PaymobPaymentService(
            api_key="test",
            integration_id="123",
            iframe_id=None,
            hmac_secret="test",
        )
        with pytest.raises(Exception):
            service.get_iframe_url("key")

    @pytest.mark.asyncio
    async def test_create_payment_intent(self):
        """Test creating a Paymob payment intent."""
        with patch.object(self.service, "_get_auth_token", new_callable=AsyncMock) as mock_auth:
            with patch.object(self.service, "_create_paymob_order", new_callable=AsyncMock) as mock_order:
                with patch.object(self.service, "_create_payment_key", new_callable=AsyncMock) as mock_key:
                    mock_auth.return_value = "auth_token_123"
                    mock_order.return_value = "order_123"
                    mock_key.return_value = "payment_key_456"

                    intent = await self.service.create_payment_intent(
                        amount=10000,  # 100 EGP
                        currency="EGP",
                        customer_email="test@example.com",
                        metadata={"order_id": "order-123"},
                    )

                    assert intent.id == "order_123"
                    assert intent.amount == 10000
                    assert intent.currency == "EGP"
                    assert intent.status == "pending"
                    assert intent.provider == PaymentProvider.PAYMOB
                    assert intent.client_secret == "payment_key_456"

    @pytest.mark.asyncio
    async def test_create_card_payment(self):
        """Test creating a card payment with PaymobPaymentKey."""
        with patch.object(self.service, "_get_auth_token", new_callable=AsyncMock) as mock_auth:
            with patch.object(self.service, "_create_paymob_order", new_callable=AsyncMock) as mock_order:
                with patch.object(self.service, "_create_payment_key", new_callable=AsyncMock) as mock_key:
                    mock_auth.return_value = "auth_token_123"
                    mock_order.return_value = "order_456"
                    mock_key.return_value = "payment_key_789"

                    result = await self.service.create_card_payment(
                        amount=50000,
                        currency="EGP",
                        customer_email="test@example.com",
                        order_id="my-order-123",
                    )

                    assert result.payment_key == "payment_key_789"
                    assert result.order_id == "order_456"
                    assert result.amount == 50000
                    assert result.provider == PaymentProvider.PAYMOB

    @pytest.mark.asyncio
    async def test_confirm_payment_success(self):
        """Test confirming a paid Paymob payment."""
        with patch.object(self.service, "_get_auth_token", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = "auth_token"

            with patch("httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "paid_amount_cents": 10000,
                    "amount_cents": 10000,
                }
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

                result = await self.service.confirm_payment("order_123")

                assert result.success is True
                assert result.payment_id == "order_123"

    @pytest.mark.asyncio
    async def test_confirm_payment_not_paid(self):
        """Test confirming an unpaid payment."""
        with patch.object(self.service, "_get_auth_token", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = "auth_token"

            with patch("httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "paid_amount_cents": 0,
                    "amount_cents": 10000,
                }
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

                result = await self.service.confirm_payment("order_123")

                assert result.success is False

    def test_verify_webhook_signature_valid(self):
        """Test verifying valid webhook signature."""
        import json

        # Create payload matching Paymob HMAC format
        payload_data = {
            "obj": {
                "amount_cents": 10000,
                "created_at": "2024-01-15T10:30:00",
                "currency": "EGP",
                "error_occured": "false",
                "has_parent_transaction": "false",
                "id": "123456",
                "integration_id": "123",
                "is_3d_secure": "true",
                "is_auth": "false",
                "is_capture": "false",
                "is_refunded": "false",
                "is_standalone_payment": "true",
                "is_voided": "false",
                "order": {"id": "789"},
                "owner": "12345",
                "pending": "false",
                "source_data": {
                    "pan": "1234",
                    "sub_type": "MasterCard",
                    "type": "card",
                },
                "success": "true",
            }
        }
        payload = json.dumps(payload_data).encode()

        # Calculate expected signature
        import hashlib
        import hmac

        obj = payload_data["obj"]
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
        expected_sig = hmac.new(
            b"test_hmac_secret",
            concatenated.encode(),
            hashlib.sha512,
        ).hexdigest()

        result = self.service.verify_webhook_signature(payload, expected_sig)
        assert result is not None
        assert result["obj"]["id"] == "123456"

    def test_verify_webhook_signature_invalid(self):
        """Test verifying invalid webhook signature."""
        payload = b'{"obj": {"id": "123"}}'
        result = self.service.verify_webhook_signature(payload, "invalid_signature")
        assert result is None

    def test_verify_webhook_signature_no_secret(self):
        """Test webhook verification without secret configured."""
        service = PaymobPaymentService(
            api_key="test",
            integration_id="123",
            iframe_id="456",
            hmac_secret=None,
        )
        result = service.verify_webhook_signature(b'{}', "sig")
        assert result is None

    @pytest.mark.asyncio
    async def test_refund_payment(self):
        """Test refunding a Paymob payment."""
        with patch.object(self.service, "_get_auth_token", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = "auth_token"

            with patch("httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"id": "refund_123"}
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

                result = await self.service.refund_payment("txn_123", amount=5000)

                assert result.success is True
                assert result.refund_id == "refund_123"

    @pytest.mark.asyncio
    async def test_cancel_payment(self):
        """Test cancelling/voiding a Paymob payment."""
        with patch.object(self.service, "_get_auth_token", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = "auth_token"

            with patch("httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

                result = await self.service.cancel_payment("order_123")

                assert result.success is True

    @pytest.mark.asyncio
    async def test_get_payment_status_paid(self):
        """Test getting payment status - paid."""
        with patch.object(self.service, "_get_auth_token", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = "auth_token"

            with patch("httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "paid_amount_cents": 10000,
                    "amount_cents": 10000,
                    "is_cancel": False,
                }
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

                status = await self.service.get_payment_status("order_123")
                assert status == "paid"

    @pytest.mark.asyncio
    async def test_get_payment_status_pending(self):
        """Test getting payment status - pending."""
        with patch.object(self.service, "_get_auth_token", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = "auth_token"

            with patch("httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "paid_amount_cents": 0,
                    "amount_cents": 10000,
                    "is_cancel": False,
                }
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

                status = await self.service.get_payment_status("order_123")
                assert status == "pending"
