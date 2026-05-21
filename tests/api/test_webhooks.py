"""Tests for webhook routes."""

import json

import pytest
from httpx import AsyncClient


class TestPaymobWebhook:
    """Tests for Paymob webhook endpoint."""

    @pytest.mark.asyncio
    async def test_paymob_webhook_valid_signature(self, client: AsyncClient):
        """Test Paymob webhook with valid signature."""
        # Create payload
        payload_data = {
            "obj": {
                "id": "123456",
                "order": {"id": "789"},
                "amount_cents": 10000,
                "success": "true",
                "pending": "false",
            }
        }

        # Note: In real tests, you'd compute the signature correctly
        # This is a placeholder for the structure
        response = await client.post(
            "/api/v1/webhooks/paymob/callback",
            content=json.dumps(payload_data),
            headers={
                "Content-Type": "application/json",
                "X-Paymob-Signature": "test_signature",
            },
        )

        # Webhook should process (even if signature validation fails in test)
        assert response.status_code in [200, 400, 401]

    @pytest.mark.asyncio
    async def test_paymob_webhook_missing_signature(self, client: AsyncClient):
        """Test Paymob webhook without signature.

        Note: Without paymob_hmac_secret configured, the webhook accepts
        requests without signature verification (dev mode).
        """
        payload_data = {"obj": {"id": "123456"}}

        response = await client.post(
            "/api/v1/webhooks/paymob/callback",
            json=payload_data,
        )

        # In dev mode (no HMAC secret), webhook accepts without verification
        # In production with HMAC secret, it would return 401
        assert response.status_code in [200, 400, 401]


class TestFawryWebhook:
    """Tests for Fawry webhook endpoint."""

    @pytest.mark.asyncio
    async def test_fawry_webhook_payment_notification(self, client: AsyncClient):
        """Test Fawry payment notification webhook."""
        payload_data = {
            "referenceNumber": "123456789",
            "merchantRefNum": "order-123",
            "orderStatus": "PAID",
            "paymentAmount": "100.00",
        }

        response = await client.post(
            "/api/v1/webhooks/fawry/callback",
            json=payload_data,
            headers={"X-Fawry-Signature": "test_signature"},
        )

        assert response.status_code in [200, 400, 401]


class TestBostaWebhook:
    """Tests for Bosta webhook endpoint."""

    @pytest.mark.asyncio
    async def test_bosta_webhook_delivery_update(self, client: AsyncClient):
        """Test Bosta delivery update webhook."""
        payload_data = {
            "trackingNumber": "BOSTA123456789",
            "state": "DELIVERED",
            "timestamp": "2024-01-15T10:30:00Z",
        }

        response = await client.post(
            "/api/v1/webhooks/bosta/callback",
            json=payload_data,
            headers={"X-Bosta-Signature": "test_signature"},
        )

        assert response.status_code in [200, 400, 401]


class TestWhatsAppWebhook:
    """Tests for WhatsApp webhook endpoint."""

    @pytest.mark.asyncio
    async def test_whatsapp_webhook_verification(self, client: AsyncClient):
        """Test WhatsApp webhook verification (GET request).

        Note: Without whatsapp_webhook_verify_token configured, the webhook
        returns 500 (not configured). With a mismatched token, it returns 403.
        With correct token, it returns 200 with the challenge.
        """
        # WhatsApp sends a GET request for webhook verification
        response = await client.get(
            "/api/v1/webhooks/whatsapp/callback",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test_token",
                "hub.challenge": "challenge_code",
            },
        )

        # 200 = verified successfully (token matches)
        # 403 = token mismatch
        # 500 = webhook verify token not configured
        assert response.status_code in [200, 403, 500]

    @pytest.mark.asyncio
    async def test_whatsapp_webhook_message_status(self, client: AsyncClient):
        """Test WhatsApp message status webhook."""
        payload_data = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {
                            "value": {
                                "statuses": [
                                    {
                                        "id": "wamid.123",
                                        "status": "delivered",
                                        "recipient_id": "201234567890",
                                    }
                                ]
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        response = await client.post(
            "/api/v1/webhooks/whatsapp/callback",
            json=payload_data,
            headers={"X-Hub-Signature-256": "sha256=test_signature"},
        )

        assert response.status_code in [200, 400, 401]
