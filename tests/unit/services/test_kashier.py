"""Unit tests for KashierPaymentService."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.interfaces.services.payment_service import PaymentProvider
from src.infrastructure.external_services.kashier.payment_service import (
    KashierPaymentService,
)


def _response(status_code: int, payload: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload if payload is not None else {}
    resp.text = json.dumps(payload or {})
    return resp


def _mock_client(*, response):
    """Patch httpx.AsyncClient so no request ever leaves the machine.

    Returns (patcher, ctx) — stop the patcher and inspect ctx.post/ctx.get.
    """
    patcher = patch("httpx.AsyncClient")
    mock = patcher.start()
    ctx = mock.return_value.__aenter__.return_value
    ctx.post = AsyncMock(return_value=response)
    ctx.get = AsyncMock(return_value=response)
    return patcher, ctx


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
    async def test_create_payment_intent_returns_session(self):
        """A created session is returned with sessionUrl as client_secret."""
        response = _response(
            201,
            {
                "_id": "sess_abc123",
                "sessionUrl": "https://payments.kashier.io/session/sess_abc123?mode=test",
            },
        )
        patcher, ctx = _mock_client(response=response)
        try:
            intent = await self.service.create_payment_intent(
                amount=10000,  # 100.00 EGP in cents
                currency="EGP",
                metadata={"order_id": "ORDER-001"},
            )
        finally:
            patcher.stop()

        assert intent.id == "sess_abc123"
        assert intent.client_secret == (
            "https://payments.kashier.io/session/sess_abc123?mode=test"
        )
        assert intent.amount == 10000
        assert intent.currency == "EGP"
        assert intent.provider == PaymentProvider.KASHIER
        assert intent.status == "pending"

        # The session is posted to the TEST host with our order reference.
        url = ctx.post.call_args[0][0]
        assert url == "https://test-api.kashier.io/v3/payment/sessions"
        payload = ctx.post.call_args.kwargs["json"]
        assert payload["merchantId"] == "MID-1234-5678"
        assert payload["order"] == "ORDER-001"
        assert ctx.post.call_args.kwargs["headers"]["api-key"] == "test_api_key_abc123"

    @pytest.mark.asyncio
    async def test_create_payment_intent_builds_session_url_when_absent(self):
        """Kashier omitting sessionUrl → we build it from the session id."""
        response = _response(201, {"_id": "sess_xyz"})
        patcher, _ = _mock_client(response=response)
        try:
            intent = await self.service.create_payment_intent(
                amount=5000,
                currency="EGP",
                metadata={"order_id": "ORDER-00X"},
            )
        finally:
            patcher.stop()

        assert intent.id == "sess_xyz"
        assert intent.client_secret == (
            "https://payments.kashier.io/session/sess_xyz?mode=test"
        )

    @pytest.mark.asyncio
    async def test_create_payment_intent_converts_cents_to_pounds(self):
        """Amount 15050 cents should be sent to Kashier as '150.50'."""
        response = _response(201, {"_id": "sess_1", "sessionUrl": "https://x/y"})
        patcher, ctx = _mock_client(response=response)
        try:
            intent = await self.service.create_payment_intent(
                amount=15050,
                currency="EGP",
                metadata={"order_id": "ORDER-002"},
            )
        finally:
            patcher.stop()

        assert ctx.post.call_args.kwargs["json"]["amount"] == "150.50"
        # The intent itself keeps cents — money is cents everywhere internally.
        assert intent.amount == 15050

    @pytest.mark.asyncio
    async def test_create_payment_intent_raises_on_rejected_session(self):
        """A non-2xx from Kashier must not be mistaken for a session."""
        response = _response(401, {"message": "No auth token provided"})
        patcher, _ = _mock_client(response=response)
        try:
            with pytest.raises(
                ValueError, match="Failed to create Kashier payment session"
            ):
                await self.service.create_payment_intent(
                    amount=1000,
                    currency="EGP",
                    metadata={"order_id": "ORDER-003"},
                )
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_create_payment_intent_raises_without_credentials(self):
        """Should raise ValueError when the API key is missing."""
        service = KashierPaymentService(mid=None, api_key=None)
        # Force None to bypass env var fallback
        service._mid = None
        service._api_key = None
        with pytest.raises(ValueError, match="Kashier API key is required"):
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
    async def test_confirm_payment_success(self):
        """A SUCCESS session status confirms the payment."""
        response = _response(200, {"paymentStatus": "SUCCESS"})
        patcher, ctx = _mock_client(response=response)
        try:
            result = await self.service.confirm_payment("sess_1")
        finally:
            patcher.stop()

        assert result.success is True
        assert result.payment_id == "sess_1"
        assert ctx.get.call_args[0][0] == (
            "https://test-api.kashier.io/v3/payment/sessions/sess_1/payment"
        )

    @pytest.mark.asyncio
    async def test_confirm_payment_non_success_status_is_not_paid(self):
        """Anything other than SUCCESS must not be treated as paid."""
        response = _response(200, {"paymentStatus": "FAILED"})
        patcher, _ = _mock_client(response=response)
        try:
            result = await self.service.confirm_payment("sess_1")
        finally:
            patcher.stop()

        assert result.success is False
        assert "FAILED" in result.error_message

    @pytest.mark.asyncio
    async def test_confirm_payment_unreachable_api_is_not_paid(self):
        """A non-200 from Kashier must never resolve to success."""
        response = _response(500, {})
        patcher, _ = _mock_client(response=response)
        try:
            result = await self.service.confirm_payment("sess_1")
        finally:
            patcher.stop()

        assert result.success is False
        assert result.error_message == "Failed to check payment status"

    @pytest.mark.asyncio
    async def test_capture_payment_delegates_to_confirm(self):
        """Kashier auto-captures — capture is a status check."""
        response = _response(200, {"paymentStatus": "SUCCESS"})
        patcher, _ = _mock_client(response=response)
        try:
            result = await self.service.capture_payment("sess_1")
        finally:
            patcher.stop()

        assert result.success is True
        assert result.payment_id == "sess_1"

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
    async def test_get_payment_status_paid(self):
        response = _response(200, {"paymentStatus": "SUCCESS"})
        patcher, _ = _mock_client(response=response)
        try:
            status = await self.service.get_payment_status("sess_1")
        finally:
            patcher.stop()

        assert status == "paid"

    @pytest.mark.asyncio
    async def test_get_payment_status_pending(self):
        response = _response(200, {"paymentStatus": "PENDING"})
        patcher, _ = _mock_client(response=response)
        try:
            status = await self.service.get_payment_status("sess_1")
        finally:
            patcher.stop()

        assert status == "pending"
