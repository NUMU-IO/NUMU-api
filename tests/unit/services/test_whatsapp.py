"""Unit tests for WhatsApp messaging service."""

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.interfaces.services.messaging_service import (
    MessageChannel,
    MessageContent,
    MessageRecipient,
    MessageStatus,
    MessageType,
)
from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
)


class TestWhatsAppMessagingService:
    """Tests for WhatsApp messaging service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = WhatsAppMessagingService(
            access_token="test_access_token",
            phone_number_id="123456789",
            business_account_id="987654321",
            app_secret="test_app_secret",
        )
        self.service.enabled = True  # Enable for testing

        self.test_recipient = MessageRecipient(
            phone="+201234567890",
            name="Test Customer",
            language="ar",
        )

    def test_channel_is_whatsapp(self):
        """Test channel property returns WHATSAPP."""
        assert self.service.channel == MessageChannel.WHATSAPP

    def test_enabled_property(self):
        """Test enabled property."""
        assert self.service.enabled is True

        self.service.enabled = False
        assert self.service.enabled is False

    def test_format_phone_number_with_plus(self):
        """Test formatting phone number with plus sign."""
        formatted = self.service._format_phone_number("+201234567890")
        assert formatted == "201234567890"

    def test_format_phone_number_without_plus(self):
        """Test formatting phone number without plus."""
        formatted = self.service._format_phone_number("201234567890")
        assert formatted == "201234567890"

    def test_format_phone_number_local(self):
        """Test formatting local Egyptian number."""
        formatted = self.service._format_phone_number("01234567890")
        assert formatted == "201234567890"

    def test_format_phone_number_with_spaces(self):
        """Test formatting phone number with spaces."""
        formatted = self.service._format_phone_number("+20 123 456 7890")
        assert formatted == "201234567890"

    @pytest.mark.asyncio
    async def test_send_message_disabled(self):
        """Test sending message when disabled."""
        self.service.enabled = False

        content = MessageContent(
            type=MessageType.ORDER_CONFIRMATION,
            recipient=self.test_recipient,
            template_params={"order_number": "123", "total": "500 EGP"},
        )

        result = await self.service.send_message(content)

        assert result.success is False
        assert result.status == MessageStatus.FAILED
        assert "disabled" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_send_message_no_credentials(self):
        """Test sending message without credentials."""
        service = WhatsAppMessagingService(
            access_token=None,
            phone_number_id=None,
        )
        service.enabled = True

        content = MessageContent(
            type=MessageType.ORDER_CONFIRMATION,
            recipient=self.test_recipient,
            template_params={"order_number": "123"},
        )

        result = await service.send_message(content)

        assert result.success is False
        assert "not configured" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """Test successful message sending."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "messages": [{"id": "wamid.123456789"}],
            }

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            content = MessageContent(
                type=MessageType.ORDER_CONFIRMATION,
                recipient=self.test_recipient,
                template_params={
                    "customer_name": "Test",
                    "order_number": "123",
                    "total": "500 EGP",
                    "store_name": "Test Store",
                },
            )

            result = await self.service.send_message(content)

            assert result.success is True
            assert result.message_id == "wamid.123456789"
            assert result.status == MessageStatus.SENT

    @pytest.mark.asyncio
    async def test_send_order_confirmation(self):
        """Test sending order confirmation."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "messages": [{"id": "wamid.order123"}],
            }

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await self.service.send_order_confirmation(
                recipient=self.test_recipient,
                order_number="ORD-123",
                total="500 EGP",
                store_name="Test Store",
            )

            assert result.success is True

    @pytest.mark.asyncio
    async def test_send_shipping_notification(self):
        """Test sending shipping notification."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "messages": [{"id": "wamid.ship123"}],
            }

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await self.service.send_shipping_notification(
                recipient=self.test_recipient,
                order_number="ORD-123",
                tracking_number="BOSTA123456789",
                carrier="Bosta",
            )

            assert result.success is True

    @pytest.mark.asyncio
    async def test_send_delivery_notification(self):
        """Test sending delivery notification."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "messages": [{"id": "wamid.deliver123"}],
            }

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await self.service.send_delivery_notification(
                recipient=self.test_recipient,
                order_number="ORD-123",
                store_name="Test Store",
            )

            assert result.success is True

    @pytest.mark.asyncio
    async def test_send_payment_received(self):
        """Test sending payment received notification."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "messages": [{"id": "wamid.pay123"}],
            }

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await self.service.send_payment_received(
                recipient=self.test_recipient,
                order_number="ORD-123",
                amount="500 EGP",
            )

            assert result.success is True

    @pytest.mark.asyncio
    async def test_send_message_api_error(self):
        """Test handling API error response."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.json.return_value = {
                "error": {
                    "message": "Invalid phone number",
                    "code": 131026,
                },
            }

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            content = MessageContent(
                type=MessageType.ORDER_CONFIRMATION,
                recipient=self.test_recipient,
                template_params={"order_number": "123"},
            )

            result = await self.service.send_message(content)

            assert result.success is False
            assert "Invalid phone number" in result.error_message

    def test_verify_webhook_signature_valid(self):
        """Test verifying valid WhatsApp webhook signature."""
        payload = b'{"object": "whatsapp_business_account"}'
        signature = (
            "sha256="
            + hmac.new(
                b"test_app_secret",
                payload,
                hashlib.sha256,
            ).hexdigest()
        )

        result = self.service.verify_webhook_signature(payload, signature)
        assert result is not None
        assert result["object"] == "whatsapp_business_account"

    def test_verify_webhook_signature_invalid(self):
        """Test verifying invalid WhatsApp webhook signature."""
        payload = b'{"object": "whatsapp_business_account"}'
        result = self.service.verify_webhook_signature(payload, "sha256=invalid")
        assert result is None

    def test_verify_webhook_signature_no_secret(self):
        """Test webhook verification without secret."""
        service = WhatsAppMessagingService(
            access_token="test",
            phone_number_id="123",
            app_secret=None,
        )
        result = service.verify_webhook_signature(b"{}", "sha256=sig")
        assert result is None

    def test_build_template_message(self):
        """Test building template message payload."""
        template = self.service._build_template_message(
            template_name="order_confirmation_ar",
            language="ar",
            parameters={
                "customer_name": "أحمد",
                "order_number": "123",
            },
        )

        assert template["name"] == "order_confirmation_ar"
        assert template["language"]["code"] == "ar"
        assert len(template["components"]) == 1
        assert template["components"][0]["type"] == "body"

    @pytest.mark.asyncio
    async def test_get_message_status(self):
        """Test getting message status."""
        status = await self.service.get_message_status("wamid.123")
        # WhatsApp doesn't have direct status check, returns SENT
        assert status == MessageStatus.SENT

    def test_handle_webhook_event(self):
        """Test handling webhook event."""
        data = {
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
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        # Should not raise
        self.service.handle_webhook_event(data)
