"""Unit tests for Paymob payment service (Intention API)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import PaymentError
from src.core.interfaces.services.payment_service import PaymentProvider
from src.infrastructure.external_services.paymob.payment_service import (
    PaymobPaymentService,
)


def _mock_client(*, response):
    """Patch httpx.AsyncClient so no request ever leaves the machine."""
    client = patch("httpx.AsyncClient")
    mock = client.start()
    ctx = mock.return_value.__aenter__.return_value
    ctx.post = AsyncMock(return_value=response)
    ctx.get = AsyncMock(return_value=response)
    return client, ctx


def _response(status_code: int, payload: dict | None = None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload if payload is not None else {}
    resp.text = text
    return resp


class TestPaymobPaymentService:
    """Tests for Paymob payment service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = PaymobPaymentService(
            secret_key="test_secret_key",
            public_key="test_public_key",
            hmac_secret="test_hmac_secret",
            card_integration_id="123456",
            wallet_integration_id="789012",
        )

    def test_provider_is_paymob(self):
        """Test provider property returns PAYMOB."""
        assert self.service.provider == PaymentProvider.PAYMOB

    @pytest.mark.asyncio
    async def test_create_payment_intent(self):
        """Test creating a Paymob intention returns the client secret."""
        response = _response(
            201,
            {
                "client_secret": "client_secret_456",
                "intention_detail": {"id": "intention_123"},
            },
        )
        patcher, _ = _mock_client(response=response)
        try:
            intent = await self.service.create_payment_intent(
                amount=10000,  # 100 EGP
                currency="EGP",
                customer_email="test@example.com",
                metadata={"order_id": "order-123"},
            )
        finally:
            patcher.stop()

        assert intent.id == "intention_123"
        assert intent.amount == 10000
        assert intent.currency == "EGP"
        assert intent.status == "pending"
        assert intent.provider == PaymentProvider.PAYMOB
        assert intent.client_secret == "client_secret_456"

    @pytest.mark.asyncio
    async def test_create_payment_intent_posts_expected_payload(self):
        """Amount stays in cents, our order id is echoed, and both
        configured integration IDs are offered as payment methods."""
        response = _response(
            201, {"client_secret": "cs", "intention_detail": {"id": "i1"}}
        )
        patcher, ctx = _mock_client(response=response)
        try:
            await self.service.create_payment_intent(
                amount=50000,
                currency="egp",
                customer_email="buyer@example.com",
                metadata={"order_id": "my-order-123"},
            )
        finally:
            patcher.stop()

        payload = ctx.post.call_args.kwargs["json"]
        assert payload["amount"] == 50000
        assert payload["currency"] == "EGP"
        assert payload["payment_methods"] == [123456, 789012]
        assert payload["merchant_order_id"] == "my-order-123"
        assert payload["special_reference"] == "my-order-123"
        assert payload["billing_data"]["email"] == "buyer@example.com"
        headers = ctx.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Token test_secret_key"

    @pytest.mark.asyncio
    async def test_create_payment_intent_without_secret_key_raises(self):
        """No secret key configured → PaymentError before any HTTP call."""
        service = PaymobPaymentService(card_integration_id="123")
        with pytest.raises(PaymentError):
            await service.create_payment_intent(amount=1000, currency="EGP")

    @pytest.mark.asyncio
    async def test_create_payment_intent_without_integration_id_raises(self):
        """No card integration ID configured → PaymentError."""
        service = PaymobPaymentService(secret_key="k")
        with pytest.raises(PaymentError):
            await service.create_payment_intent(amount=1000, currency="EGP")

    @pytest.mark.asyncio
    async def test_create_payment_intent_surfaces_paymob_detail(self):
        """A rejection surfaces Paymob's own reason to the caller."""
        response = _response(
            400,
            {"detail": "incorrect combination of Integration ID + Currency"},
            text='{"detail": "..."}',
        )
        patcher, _ = _mock_client(response=response)
        try:
            with pytest.raises(PaymentError, match="incorrect combination"):
                await self.service.create_payment_intent(
                    amount=1000, currency="EGP", metadata={"order_id": "o1"}
                )
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_confirm_payment_success(self):
        """Test confirming a confirmed Paymob intention."""
        response = _response(200, {"intention_detail": {"status": "confirmed"}})
        patcher, _ = _mock_client(response=response)
        try:
            result = await self.service.confirm_payment("intention_123")
        finally:
            patcher.stop()

        assert result.success is True
        assert result.payment_id == "intention_123"

    @pytest.mark.asyncio
    async def test_confirm_payment_not_paid(self):
        """Test confirming an unpaid intention."""
        response = _response(200, {"intention_detail": {"status": "pending"}})
        patcher, _ = _mock_client(response=response)
        try:
            result = await self.service.confirm_payment("intention_123")
        finally:
            patcher.stop()

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
            secret_key="test",
            card_integration_id="123",
            hmac_secret=None,
        )
        result = service.verify_webhook_signature(b"{}", "sig")
        assert result is None

    @pytest.mark.asyncio
    async def test_refund_payment(self):
        """Test refunding a Paymob payment."""
        response = _response(200, {"id": "refund_123"})
        patcher, ctx = _mock_client(response=response)
        try:
            result = await self.service.refund_payment("txn_123", amount=5000)
        finally:
            patcher.stop()

        assert result.success is True
        assert result.refund_id == "refund_123"
        # The refund is scoped to the transaction and the requested amount.
        body = ctx.post.call_args.kwargs["json"]
        assert body["transaction_id"] == "txn_123"
        assert body["amount_cents"] == 5000

    @pytest.mark.asyncio
    async def test_cancel_payment(self):
        """Test cancelling/voiding a Paymob payment."""
        response = _response(200, {})
        patcher, _ = _mock_client(response=response)
        try:
            result = await self.service.cancel_payment("order_123")
        finally:
            patcher.stop()

        assert result.success is True

    @pytest.mark.asyncio
    async def test_get_payment_status_paid(self):
        """Test getting payment status - paid."""
        response = _response(200, {"intention_detail": {"status": "confirmed"}})
        patcher, _ = _mock_client(response=response)
        try:
            status = await self.service.get_payment_status("intention_123")
        finally:
            patcher.stop()

        assert status == "paid"

    @pytest.mark.asyncio
    async def test_get_payment_status_pending(self):
        """Test getting payment status - pending."""
        response = _response(200, {"intention_detail": {"status": "pending"}})
        patcher, _ = _mock_client(response=response)
        try:
            status = await self.service.get_payment_status("intention_123")
        finally:
            patcher.stop()

        assert status == "pending"

    @pytest.mark.asyncio
    async def test_get_payment_status_cancelled(self):
        """Test getting payment status - voided/cancelled."""
        response = _response(200, {"intention_detail": {"status": "voided"}})
        patcher, _ = _mock_client(response=response)
        try:
            status = await self.service.get_payment_status("intention_123")
        finally:
            patcher.stop()

        assert status == "cancelled"
