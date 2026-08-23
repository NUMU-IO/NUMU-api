"""Facebook Messenger client for sending messages."""

from typing import Any

from src.core.logging import get_logger
from src.infrastructure.external_services.meta.graph_client import MetaGraphClient

logger = get_logger(__name__)


class MessengerClient:
    """Client for Facebook Messenger send API."""

    def __init__(self, page_id: str, page_access_token: str):
        self.page_id = page_id
        self.client = MetaGraphClient(page_access_token)

    async def close(self) -> None:
        await self.client.close()

    async def send_text(
        self,
        recipient_id: str,
        text: str,
        tag: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message to a user.

        ``tag`` switches to MESSAGE_TAG delivery (e.g. POST_PURCHASE_UPDATE
        for order/payment updates), which Meta allows outside the standard
        24-hour messaging window for the tag's approved use cases.
        """
        endpoint = f"{self.page_id}/messages"
        data = {
            "messaging_type": "MESSAGE_TAG" if tag else "RESPONSE",
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        }
        if tag:
            data["tag"] = tag

        logger.info(
            "messenger_send_text", recipient_id=recipient_id, text_length=len(text)
        )

        return await self.client.post(endpoint, data)

    async def send_attachment(
        self,
        recipient_id: str,
        attachment_type: str,
        attachment_url: str,
        recipient_type: str = "user",
    ) -> dict[str, Any]:
        """Send an attachment (image, video, audio, file)."""
        endpoint = f"{self.page_id}/messages"
        data = {
            "messaging_type": "RESPONSE",
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": attachment_type,
                    "payload": {
                        "url": attachment_url,
                        "is_reusable": True,
                    },
                }
            },
        }

        logger.info(
            "messenger_send_attachment",
            recipient_id=recipient_id,
            attachment_type=attachment_type,
        )

        return await self.client.post(endpoint, data)

    async def send_product(
        self,
        recipient_id: str,
        product_id: str,
    ) -> dict[str, Any]:
        """Send a product card (requires Facebook Shop setup)."""
        endpoint = f"{self.page_id}/messages"
        data = {
            "messaging_type": "RESPONSE",
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "product",
                        "product_id": product_id,
                    },
                }
            },
        }

        logger.info(
            "messenger_send_product", recipient_id=recipient_id, product_id=product_id
        )

        return await self.client.post(endpoint, data)

    async def mark_seen(self, recipient_id: str) -> dict[str, Any]:
        """Mark messages as seen (send typing indicator)."""
        endpoint = f"{self.page_id}/messages"
        data = {
            "recipient": {"id": recipient_id},
            "sender_action": "mark_seen",
        }

        logger.debug("messenger_mark_seen", recipient_id=recipient_id)

        return await self.client.post(endpoint, data)

    async def send_typing_indicator(
        self, recipient_id: str, state: bool = True
    ) -> dict[str, Any]:
        """Send typing indicator on/off."""
        endpoint = f"{self.page_id}/messages"
        data = {
            "recipient": {"id": recipient_id},
            "sender_action": "typing_on" if state else "typing_off",
        }

        logger.debug("messenger_typing", recipient_id=recipient_id, state=state)

        return await self.client.post(endpoint, data)

    async def get_conversation_history(
        self,
        recipient_id: str,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Get conversation history with a user."""
        endpoint = f"{self.page_id}/conversations"
        params = {
            "fields": "messages{message,from,to,created_time,attachments}",
            "recipient": {"id": recipient_id},
        }

        logger.debug("messenger_get_history", recipient_id=recipient_id)

        data = await self.client.get(endpoint, params)
        messages: list[dict[str, Any]] = data.get("messages", {}).get("data", [])
        return messages[:limit]
