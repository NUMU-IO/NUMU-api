"""WhatsApp template management via Meta Graph API.

Handles creating, listing, and syncing message template statuses
with Meta's WhatsApp Business Platform.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v18.0"


class WhatsAppTemplateService:
    """Manages WhatsApp message templates via Meta Graph API."""

    def __init__(self, access_token: str, waba_id: str) -> None:
        self.access_token = access_token
        self.waba_id = waba_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def list_templates(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch all templates from Meta for this WABA."""
        url = f"{GRAPH_API_BASE}/{self.waba_id}/message_templates"
        params = {"limit": limit}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url, headers=self._headers(), params=params, timeout=30.0
                )
                if resp.status_code == 200:
                    return resp.json().get("data", [])
                logger.warning(
                    "Failed to list templates: %s %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return []
        except Exception as e:
            logger.error("Template list error: %s", e)
            return []

    async def create_template(
        self,
        name: str,
        language: str,
        category: str,
        body_text: str,
        header_type: str | None = None,
        header_content: str | None = None,
        footer_text: str | None = None,
        buttons: list[dict] | None = None,
    ) -> dict[str, Any] | None:
        """Submit a new template to Meta for approval.

        Returns the Meta response dict on success, None on failure.
        """
        url = f"{GRAPH_API_BASE}/{self.waba_id}/message_templates"

        components: list[dict[str, Any]] = []

        # Header component
        if header_type and header_content:
            if header_type.upper() == "TEXT":
                components.append({
                    "type": "HEADER",
                    "format": "TEXT",
                    "text": header_content,
                })
            elif header_type.upper() in ("IMAGE", "VIDEO", "DOCUMENT"):
                components.append({
                    "type": "HEADER",
                    "format": header_type.upper(),
                    "example": {"header_handle": [header_content]},
                })

        # Body component
        components.append({"type": "BODY", "text": body_text})

        # Footer component
        if footer_text:
            components.append({"type": "FOOTER", "text": footer_text})

        # Buttons
        if buttons:
            button_components = []
            for btn in buttons:
                btn_obj: dict[str, Any] = {"type": btn["type"], "text": btn["text"]}
                if btn.get("url"):
                    btn_obj["url"] = btn["url"]
                if btn.get("phone_number"):
                    btn_obj["phone_number"] = btn["phone_number"]
                button_components.append(btn_obj)
            components.append({"type": "BUTTONS", "buttons": button_components})

        payload = {
            "name": name,
            "language": language,
            "category": category,
            "components": components,
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url, headers=self._headers(), json=payload, timeout=30.0
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    logger.info("Template created: %s (id=%s)", name, data.get("id"))
                    return data
                logger.warning(
                    "Template creation failed: %s %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return None
        except Exception as e:
            logger.error("Template creation error: %s", e)
            return None

    async def delete_template(self, template_name: str) -> bool:
        """Delete a template by name from Meta."""
        url = f"{GRAPH_API_BASE}/{self.waba_id}/message_templates"
        params = {"name": template_name}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    url, headers=self._headers(), params=params, timeout=30.0
                )
                if resp.status_code == 200:
                    logger.info("Template deleted: %s", template_name)
                    return True
                logger.warning(
                    "Template deletion failed: %s %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False
        except Exception as e:
            logger.error("Template deletion error: %s", e)
            return False

    async def sync_statuses(
        self, local_templates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Sync local template statuses with Meta.

        Args:
            local_templates: List of dicts with at least 'name' and 'language' keys.

        Returns:
            List of dicts with 'name', 'language', 'status', and 'rejection_reason'.
        """
        meta_templates = await self.list_templates()
        meta_index: dict[tuple[str, str], dict] = {}
        for t in meta_templates:
            meta_index[(t["name"], t["language"])] = t

        updates = []
        for local in local_templates:
            key = (local["name"], local["language"])
            meta = meta_index.get(key)
            if meta:
                updates.append({
                    "name": local["name"],
                    "language": local["language"],
                    "status": meta.get("status", "PENDING").upper(),
                    "meta_template_id": meta.get("id"),
                    "rejection_reason": meta.get("rejected_reason"),
                })
        return updates
