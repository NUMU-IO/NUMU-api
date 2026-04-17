"""Meta Conversions API (CAPI) client for server-side event tracking."""

import hashlib
from typing import Any
from uuid import UUID

from src.config.logging_config import get_logger
from src.infrastructure.external_services.meta.graph_client import MetaGraphClient

logger = get_logger(__name__)


def hash_email(email: str) -> str:
    """Hash email using SHA-256 (lowercase)."""
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()


def hash_phone(phone: str) -> str:
    """Hash phone number using SHA-256 (digits only, lowercase)."""
    digits = "".join(c for c in phone if c.isdigit())
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()


def hash_name(name: str) -> str:
    """Hash name using SHA-256 (lowercase)."""
    return hashlib.sha256(name.lower().encode("utf-8")).hexdigest()


class CapiClient:
    """Client for Meta Conversions API."""

    def __init__(
        self,
        pixel_id: str,
        access_token: str,
        test_event_code: str | None = None,
    ):
        self.pixel_id = pixel_id
        self.access_token = access_token
        self.test_event_code = test_event_code
        self.client = MetaGraphClient(access_token)

    async def close(self) -> None:
        await self.client.close()

    async def send_event(
        self,
        event_name: str,
        event_time: int,
        user_data: dict[str, Any],
        event_id: str | None = None,
        event_source_url: str | None = None,
        custom_data: dict[str, Any] | None = None,
        action_source: str = "WEBSITE",
    ) -> dict[str, Any]:
        """Send a conversion event to CAPI."""
        endpoint = f"{self.pixel_id}/events"

        user_data_hashed = {}
        if email := user_data.get("email"):
            user_data_hashed["em"] = [hash_email(email)]
        if phone := user_data.get("phone"):
            user_data_hashed["ph"] = [hash_phone(phone)]
        if first_name := user_data.get("first_name"):
            user_data_hashed["fn"] = [hash_name(first_name)]
        if last_name := user_data.get("last_name"):
            user_data_hashed["ln"] = [hash_name(last_name)]
        if external_id := user_data.get("external_id"):
            user_data_hashed["external_id"] = [str(external_id)]

        data: dict[str, Any] = {
            "data": [
                {
                    "event_name": event_name,
                    "event_time": event_time,
                    "action_source": action_source,
                    "user_data": user_data_hashed,
                }
            ]
        }

        if event_id:
            data["data"][0]["event_id"] = str(event_id)
        if event_source_url:
            data["data"][0]["event_source_url"] = event_source_url
        if custom_data:
            data["data"][0]["custom_data"] = custom_data
        if self.test_event_code:
            data["test_event_code"] = self.test_event_code

        logger.info(
            "capi_send_event",
            event_name=event_name,
            pixel_id=self.pixel_id,
            has_email=bool(user_data.get("email")),
            has_phone=bool(user_data.get("phone")),
        )

        return await self.client.post(endpoint, data)

    async def send_purchase(
        self,
        value: int,
        currency: str,
        user_data: dict[str, Any],
        event_time: int,
        event_id: UUID | None = None,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a Purchase event."""
        custom_data = {
            "value": value,
            "currency": currency,
        }
        if order_id:
            custom_data["order_id"] = order_id

        return await self.send_event(
            event_name="Purchase",
            event_time=event_time,
            user_data=user_data,
            event_id=str(event_id) if event_id else None,
            custom_data=custom_data,
        )

    async def send_initiate_checkout(
        self,
        value: int,
        currency: str,
        user_data: dict[str, Any],
        event_time: int,
        event_id: UUID | None = None,
        content_type: str | None = None,
        content_ids: list[str] | None = None,
        num_items: int | None = None,
    ) -> dict[str, Any]:
        """Send an InitiateCheckout event."""
        custom_data = {
            "value": value,
            "currency": currency,
        }
        if content_type:
            custom_data["content_type"] = content_type
        if content_ids:
            custom_data["content_ids"] = content_ids
        if num_items is not None:
            custom_data["num_items"] = num_items

        return await self.send_event(
            event_name="InitiateCheckout",
            event_time=event_time,
            user_data=user_data,
            event_id=str(event_id) if event_id else None,
            custom_data=custom_data,
        )

    async def send_view_content(
        self,
        product_id: str,
        value: float,
        currency: str,
        user_data: dict[str, Any],
        event_time: int,
        content_type: str = "product",
    ) -> dict[str, Any]:
        """Send a ViewContent event."""
        custom_data = {
            "content_ids": [product_id],
            "content_type": content_type,
            "value": value,
            "currency": currency,
        }

        return await self.send_event(
            event_name="ViewContent",
            event_time=event_time,
            user_data=user_data,
            custom_data=custom_data,
        )

    async def send_add_to_cart(
        self,
        product_id: str,
        value: float,
        currency: str,
        user_data: dict[str, Any],
        event_time: int,
        quantity: int = 1,
    ) -> dict[str, Any]:
        """Send an AddToCart event."""
        custom_data = {
            "content_ids": [product_id],
            "content_type": "product",
            "value": value,
            "currency": currency,
            "quantity": quantity,
        }

        return await self.send_event(
            event_name="AddToCart",
            event_time=event_time,
            user_data=user_data,
            custom_data=custom_data,
        )

    async def test_event(self) -> dict[str, Any]:
        """Test that CAPI is working."""
        import time

        return await self.send_event(
            event_name="TestEvent",
            event_time=int(time.time()),
            user_data={"email": "test@example.com"},
        )
