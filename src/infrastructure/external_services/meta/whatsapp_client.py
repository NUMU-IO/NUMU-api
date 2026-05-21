"""WhatsApp Cloud API client."""

from typing import Any

import httpx

from src.config import settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)


class WhatsAppClient:
    """Client for WhatsApp Cloud API."""

    def __init__(
        self,
        phone_number_id: str,
        access_token: str,
        waba_id: str | None = None,
    ):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.waba_id = waba_id
        self.api_version = settings.whatsapp_business_api_version or "v21.0"
        self.base_url = f"https://graph.facebook.com/{self.api_version}"
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def send_text(
        self,
        recipient_phone: str,
        text: str,
        preview_url: bool = False,
    ) -> dict[str, Any]:
        """Send a text message."""
        endpoint = f"{self.phone_number_id}/messages"
        data: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "text",
            "text": {"body": text},
        }
        if preview_url:
            data["text"]["preview_url"] = True

        logger.info(
            "whatsapp_send_text", phone=recipient_phone[-4:], text_length=len(text)
        )

        response = await self._client.post(
            f"{self.base_url}/{endpoint}",
            headers=self._get_headers(),
            json=data,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def send_image(
        self,
        recipient_phone: str,
        image_url: str | None = None,
        image_id: str | None = None,
        caption: str | None = None,
    ) -> dict[str, Any]:
        """Send an image message."""
        endpoint = f"{self.phone_number_id}/messages"
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "image",
        }

        if image_url:
            payload["image"] = {"link": image_url, "caption": caption}
        elif image_id:
            payload["image"] = {"id": image_id, "caption": caption}
        else:
            raise ValueError("Either image_url or image_id required")

        logger.info(
            "whatsapp_send_image", phone=recipient_phone[-4:], has_caption=bool(caption)
        )

        response = await self._client.post(
            f"{self.base_url}/{endpoint}",
            headers=self._get_headers(),
            json=payload,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def send_document(
        self,
        recipient_phone: str,
        document_url: str | None = None,
        document_id: str | None = None,
        caption: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Send a document."""
        endpoint = f"{self.phone_number_id}/messages"
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "document",
        }

        if document_url:
            doc = {"link": document_url, "caption": caption}
            if filename:
                doc["filename"] = filename
            payload["document"] = doc
        elif document_id:
            doc = {"id": document_id, "caption": caption}
            if filename:
                doc["filename"] = filename
            payload["document"] = doc
        else:
            raise ValueError("Either document_url or document_id required")

        logger.info(
            "whatsapp_send_document", phone=recipient_phone[-4:], filename=filename
        )

        response = await self._client.post(
            f"{self.base_url}/{endpoint}",
            headers=self._get_headers(),
            json=payload,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def send_template(
        self,
        recipient_phone: str,
        template_name: str,
        language: str = "ar_AR",
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a template message."""
        endpoint = f"{self.phone_number_id}/messages"
        data: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
            },
        }
        if components:
            data["template"]["components"] = components

        logger.info(
            "whatsapp_send_template", phone=recipient_phone[-4:], template=template_name
        )

        response = await self._client.post(
            f"{self.base_url}/{endpoint}",
            headers=self._get_headers(),
            json=data,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def mark_read(self, message_id: str) -> dict[str, Any]:
        """Mark message as read."""
        endpoint = f"{self.phone_number_id}/messages"
        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }

        logger.debug("whatsapp_mark_read", message_id=message_id)

        response = await self._client.post(
            f"{self.base_url}/{endpoint}",
            headers=self._get_headers(),
            json=data,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def upload_media(self, media_url: str, media_type: str) -> dict[str, Any]:
        """Upload media to WhatsApp servers."""
        endpoint = f"{self.phone_number_id}/media"
        data = {
            "messaging_product": "whatsapp",
            "file_url": media_url,
            "type": media_type,
        }

        logger.info("whatsapp_upload_media", media_type=media_type)

        response = await self._client.post(
            f"{self.base_url}/{endpoint}",
            headers=self._get_headers(),
            json=data,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def download_media(self, media_id: str) -> dict[str, Any]:
        """Get media download URL."""
        endpoint = f"{media_id}"
        params = {"access_token": self.access_token}

        logger.debug("whatsapp_get_media", media_id=media_id)

        response = await self._client.get(
            f"{self.base_url}/{endpoint}",
            headers=self._get_headers(),
            params=params,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def create_interactive_list(
        self,
        recipient_phone: str,
        title: str,
        button_text: str,
        sections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create and send interactive list message."""
        endpoint = f"{self.phone_number_id}/messages"
        data = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": title},
                "body": {"text": title},
                "action": {"button": button_text, "sections": sections},
            },
        }

        logger.info(
            "whatsapp_send_list", phone=recipient_phone[-4:], sections=len(sections)
        )

        response = await self._client.post(
            f"{self.base_url}/{endpoint}",
            headers=self._get_headers(),
            json=data,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result
