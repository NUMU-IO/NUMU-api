"""Instagram DM client for sending messages."""

from typing import Any

from src.config.logging_config import get_logger
from src.infrastructure.external_services.meta.graph_client import MetaGraphClient

logger = get_logger(__name__)


class InstagramClient:
    """Client for Instagram Direct Message API."""

    def __init__(self, ig_user_id: str, access_token: str):
        self.ig_user_id = ig_user_id
        self.client = MetaGraphClient(access_token)

    async def close(self) -> None:
        await self.client.close()

    async def send_text(
        self,
        recipient_igid: str,
        text: str,
    ) -> dict[str, Any]:
        """Send a text message to an Instagram user."""
        endpoint = f"{self.ig_user_id}/messages"
        data = {
            "recipient": {"igid": recipient_igid},
            "message": {"text": text},
        }

        logger.info(
            "instagram_send_text", recipient_id=recipient_igid, text_length=len(text)
        )

        return await self.client.post(endpoint, data)

    async def send_attachment(
        self,
        recipient_igid: str,
        attachment_type: str,
        attachment_url: str,
    ) -> dict[str, Any]:
        """Send an attachment (image, video)."""
        endpoint = f"{self.ig_user_id}/messages"
        data = {
            "recipient": {"igid": recipient_igid},
            "message": {
                "attachment": {
                    "type": attachment_type,
                    "payload": {"url": attachment_url},
                }
            },
        }

        logger.info(
            "instagram_send_attachment",
            recipient_id=recipient_igid,
            attachment_type=attachment_type,
        )

        return await self.client.post(endpoint, data)

    async def send_product(
        self,
        recipient_igid: str,
        product_id: str,
    ) -> dict[str, Any]:
        """Send a product catalog item."""
        endpoint = f"{self.ig_user_id}/messages"
        data = {
            "recipient": {"igid": recipient_igid},
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "instagram_product",
                        "product_id": product_id,
                    },
                }
            },
        }

        logger.info(
            "instagram_send_product", recipient_id=recipient_igid, product_id=product_id
        )

        return await self.client.post(endpoint, data)

    async def mark_seen(self, recipient_igid: str, message_id: str) -> dict[str, Any]:
        """Mark a message as seen."""
        endpoint = f"{self.ig_user_id}/messages"
        data = {
            "recipient": {"igid": recipient_igid},
            "message": {"mark_seen": {"message_id": message_id}},
        }

        logger.debug(
            "instagram_mark_seen", recipient_id=recipient_igid, message_id=message_id
        )

        return await self.client.post(endpoint, data)

    async def get_user_profile(self, igid: str) -> dict[str, Any]:
        """Get Instagram user profile info."""
        endpoint = f"{igid}"
        params = {
            "fields": "id,username,name,profile_picture_url,account_type,media_count",
        }

        logger.debug("instagram_get_profile", igid=igid)

        return await self.client.get(endpoint, params)

    async def get_conversation(
        self,
        thread_id: str,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Get conversation messages."""
        endpoint = f"{thread_id}"
        params = {
            "fields": "messages{id,created_time,from,to,message,share,attachments}",
            "limit": limit,
        }

        logger.debug("instagram_get_conversation", thread_id=thread_id)

        data = await self.client.get(endpoint, params)
        result: list[dict[str, Any]] = data.get("messages", {}).get("data", [])
        return result
