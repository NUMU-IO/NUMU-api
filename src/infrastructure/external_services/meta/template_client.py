"""WhatsApp template client for managing templates."""

from typing import Any

from src.config.logging_config import get_logger
from src.infrastructure.external_services.meta.graph_client import MetaGraphClient

logger = get_logger(__name__)


class TemplateClient:
    """Client for WhatsApp Business Management API - Templates."""

    def __init__(self, waba_id: str, access_token: str):
        self.waba_id = waba_id
        self.client = MetaGraphClient(access_token)

    async def close(self) -> None:
        await self.client.close()

    async def list_templates(
        self,
        limit: int = 25,
        after: str | None = None,
    ) -> dict[str, Any]:
        """List all templates in the WABA."""
        endpoint = f"{self.waba_id}/message_templates"
        params = {
            "fields": "id,name,category,language,status,components,rejection_reason,created_at",
            "limit": limit,
        }
        if after:
            params["after"] = after

        logger.debug("wa_list_templates", waba_id=self.waba_id, limit=limit)

        return await self.client.get(endpoint, params)

    async def get_template(self, template_id: str) -> dict[str, Any]:
        """Get a specific template."""
        endpoint = f"{template_id}"
        params = {
            "fields": "id,name,category,language,status,components,rejection_reason,created_at,updated_at",
        }

        logger.debug("wa_get_template", template_id=template_id)

        return await self.client.get(endpoint, params)

    async def create_template(
        self,
        name: str,
        category: str,
        language: str,
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a new template."""
        endpoint = f"{self.waba_id}/message_templates"
        data = {
            "name": name,
            "category": category,
            "language": language,
            "components": components,
        }

        logger.info(
            "wa_create_template", name=name, category=category, language=language
        )

        return await self.client.post(endpoint, data)

    async def update_template(
        self,
        template_id: str,
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Update template components (requires PENDING or APPROVED status)."""
        endpoint = template_id
        data = {
            "components": components,
        }

        logger.info("wa_update_template", template_id=template_id)

        return await self.client.post(endpoint, data)

    async def delete_template(self, template_id: str) -> bool:
        """Delete a template (only DRAFT templates can be deleted)."""
        endpoint = template_id

        logger.info("wa_delete_template", template_id=template_id)

        await self.client.delete(endpoint)
        return True

    def build_text_template(
        self,
        header: str | None = None,
        body: str | None = None,
        footer: str | None = None,
        buttons: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build components for a text-based template."""
        components: list[dict[str, Any]] = []

        if header:
            components.append({"type": "HEADER", "text": header})

        if body:
            components.append({"type": "BODY", "text": body})

        if footer:
            components.append({"type": "FOOTER", "text": footer})

        if buttons:
            components.append({"type": "BUTTONS", "buttons": buttons})

        return components

    def build_media_template(
        self,
        media_type: str,
        media_url: str,
        header_text: str | None = None,
        body: str | None = None,
        footer: str | None = None,
        buttons: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build components for a media-based template."""
        components: list[dict[str, Any]] = []

        header_type = (
            "IMAGE"
            if media_type == "image"
            else "VIDEO"
            if media_type == "video"
            else "DOCUMENT"
        )
        header_obj = {"type": header_type, "media_url": media_url}
        if header_text:
            header_obj["text"] = header_text
        components.append({"type": "HEADER", **header_obj})

        if body:
            components.append({"type": "BODY", "text": body})

        if footer:
            components.append({"type": "FOOTER", "text": footer})

        if buttons:
            components.append({"type": "BUTTONS", "buttons": buttons})

        return components
