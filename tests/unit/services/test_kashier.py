"""Unit tests for KashierPaymentService."""

import hashlib
import hmac
import json

import pytest

from src.core.interfaces.services.payment_service import PaymentProvider
from src.infrastructure.external_services.kashier.payment_service import (
    KashierPaymentService,
)


class TestKashierPaymentService:
    """Tests for KashierPaymentService."""

    def setup_method(self):
        self.service = KashierPaymentService(
            mid="MID-1234-5678",
            api_key="test_api_key_abc123",
            mode="test",
            currency="EGP",
        )

    # -- provider property ------------------------------------------------

    def test_provider_returns_kashier(self):
        assert self.service.provider == PaymentProvider.KASHIER

    # -- create_payment_intent --------------------------------------------

    @pytest.mark.asyncio
    async def test_create_payment_intent_generates_correct_hash(self):
        """Verify HMAC SHA256 hash matches expected value."""
        intent = await self.service.create_payment_intent(
            amount=10000,  # 100.00 EGP in cents
            currency="EGP",
            metadata={"order_id": "ORDER-001"},
        )

        # Manually compute expected hash
        path = "/?payment=MID-1234-5678.ORDER-001.100.00.EGP"
        expected_hash = hmac.new(
            b"test_api_key_abc123", path.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        assert intent.id == "ORDER-001"
        assert intent.client_secret == expected_hash
        assert intent.amount == 10000
        assert intent.currency == "EGP"
        assert intent.provider == PaymentProvider.KASHIER
        assert intent.status == "pending"

    @pytest.mark.asyncio
    async def test_create_payment_intent_without_order_id_generates_uuid(self):
        """When no order_id in metadata, a UUID is generated."""
        intent = await self.service.create_payment_intent(amount=5000, currency="EGP")
        assert intent.id  # Should be a generated UUID string
        assert intent.client_secret  # Should be a valid hex hash
        assert len(intent.client_secret) == 64  # SHA256 hex length

    @pytest.mark.asyncio
    async def test_create_payment_intent_converts_cents_to_pounds(self):
        """Amount 15050 cents should become '150.50' in the hash path."""
        intent = await self.service.create_payment_intent(
            amount=15050,
            currency="EGP",
            metadata={"order_id": "ORDER-002"},
        )
        path = "/?payment=MID-1234-5678.ORDER-002.150.50.EGP"
        expected_hash = hmac.new(
            b"test_api_key_abc123", path.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        assert intent.client_secret == expected_hash

    @pytest.mark.asyncio
    async def test_create_payment_intent_raises_without_credentials(self):
        """Should raise ValueError when MID or API key is missing."""
        service = KashierPaymentService(mid=None, api_key=None)
        # Force None to bypass env var fallback
        service._mid = None
        service._api_key = None
        with pytest.raises(ValueError, match="Kashier MID and API key are required"):
            await service.create_payment_intent(amount=1000, currency="EGP")

    # -- verify_webhook_signature -----------------------------------------

    def test_verify_webhook_valid_signature(self):
        """Valid HMAC should return parsed payload."""
        payload_dict = {
            "paymentStatus": "SUCCESS",
            "cardDataToken": "tok_123",
            "maskedCard": "****1234",
            "merchantOrderId": "ORDER-001",
            "orderId": "KSH-001",
            "cardBrand": "Visa",
            "orderReference": "ref-001",
            "transactionId": "txn-001",
            "amount": "100.00",
            "currency": "EGP",
        }

        # Compute the correct signature
        query_string = (
            f"paymentStatus={payload_dict['paymentStatus']}"
            f"&cardDataToken={payload_dict['cardDataToken']}"
            f"&maskedCard={payload_dict['maskedCard']}"
            f"&merchantOrderId={payload_dict['merchantOrderId']}"
            f"&orderId={payload_dict['orderId']}"
            f"&cardBrand={payload_dict['cardBrand']}"
            f"&orderReference={payload_dict['orderReference']}"
            f"&transactionId={payload_dict['transactionId']}"
            f"&amount={payload_dict['amount']}"
            f"&currency={payload_dict['currency']}"
        )
        valid_sig = hmac.new(
            b"test_api_key_abc123",
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        result = self.service.verify_webhook_signature(
            json.dumps(payload_dict).encode("utf-8"),
            valid_sig,
        )
        assert result is not None
        assert result["paymentStatus"] == "SUCCESS"
        assert result["merchantOrderId"] == "ORDER-001"

    def test_verify_webhook_invalid_signature(self):
        """Invalid HMAC should return None."""
        payload_dict = {
            "paymentStatus": "SUCCESS",
            "cardDataToken": "",
            "maskedCard": "",
            "merchantOrderId": "ORDER-001",
            "orderId": "KSH-001",
            "cardBrand": "",
            "orderReference": "",
            "transactionId": "txn-001",
            "amount": "100.00",
            "currency": "EGP",
        }
        result = self.service.verify_webhook_signature(
            json.dumps(payload_dict).encode("utf-8"),
            "invalid_signature_value",
        )
        assert result is None

    def test_verify_webhook_malformed_json(self):
        """Malformed JSON payload should return None."""
        result = self.service.verify_webhook_signature(b"not valid json", "any_sig")
        assert result is None

    def test_verify_webhook_missing_fields_uses_empty_string(self):
        """Missing optional fields should default to empty string in hash."""
        payload_dict = {
            "paymentStatus": "FAILED",
            "merchantOrderId": "ORDER-003",
            "orderId": "KSH-003",
            "transactionId": "txn-003",
            "amount": "50.00",
            "currency": "EGP",
        }

        # Compute signature with missing fields defaulting to empty
        query_string = (
            f"paymentStatus={payload_dict['paymentStatus']}"
            f"&cardDataToken="
            f"&maskedCard="
            f"&merchantOrderId={payload_dict['merchantOrderId']}"
            f"&orderId={payload_dict['orderId']}"
            f"&cardBrand="
            f"&orderReference="
            f"&transactionId={payload_dict['transactionId']}"
            f"&amount={payload_dict['amount']}"
            f"&currency={payload_dict['currency']}"
        )
        valid_sig = hmac.new(
            b"test_api_key_abc123",
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        result = self.service.verify_webhook_signature(
            json.dumps(payload_dict).encode("utf-8"),
            valid_sig,
        )
        assert result is not None
        assert result["paymentStatus"] == "FAILED"

    def test_verify_webhook_no_api_key_returns_none(self):
        """Service without API key should return None."""
        service = KashierPaymentService(mid="MID-1234-5678", api_key=None)
        # Force _api_key to None (settings fallback may provide a value)
        service._api_key = None
        result = service.verify_webhook_signature(b'{"test": true}', "sig")
        assert result is None

    # -- confirm/capture/cancel/refund ------------------------------------

    @pytest.mark.asyncio
    async def test_confirm_payment_not_applicable(self):
        result = await self.service.confirm_payment("intent-1")
        assert result.success is False
        assert result.error_code == "NOT_APPLICABLE"

    @pytest.mark.asyncio
    async def test_capture_payment_not_supported(self):
        result = await self.service.capture_payment("intent-1")
        assert result.success is False
        assert result.error_code == "NOT_SUPPORTED"

    @pytest.mark.asyncio
    async def test_cancel_payment_not_supported(self):
        result = await self.service.cancel_payment("intent-1")
        assert result.success is False
        assert result.error_code == "NOT_SUPPORTED"

    @pytest.mark.asyncio
    async def test_refund_payment_not_supported(self):
        result = await self.service.refund_payment("payment-1")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_get_payment_status_returns_unknown(self):
        status = await self.service.get_payment_status("payment-1")
        assert status == "unknown"
