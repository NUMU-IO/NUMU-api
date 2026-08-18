"""Meta Conversions API (CAPI) client for server-side event tracking."""

from typing import Any
from uuid import UUID

from src.core.logging import get_logger
from src.infrastructure.external_services.meta.graph_client import MetaGraphClient

logger = get_logger(__name__)


# Kept as thin wrappers over the ONE hashing implementation
# (`meta/hashing.py`). They used to normalize differently from it, which meant
# the omnichannel WhatsApp CAPI path and the storefront CAPI path sent
# DIFFERENT digests for the same shopper — so Meta saw two people:
#
#   * `hash_phone` stripped to digits with no country code, so an Egyptian
#     "01001234567" hashed as `01001234567` while the main path sends
#     `201001234567`. Never matched.
#   * `hash_name` lowercased but kept punctuation, against Meta's
#     "no punctuation" rule.
#   * `external_id` was not hashed at all — sent as raw plaintext.
#
# One implementation is the point: normalization only works if every producer
# agrees on it.


def hash_email(email: str) -> str:
    """SHA-256 of a trimmed, lowercased email — Meta's `em` rule."""
    from src.infrastructure.external_services.meta.hashing import _h

    return _h(email) or ""


def hash_phone(phone: str) -> str:
    """SHA-256 of the MENA-normalized E.164-without-plus form."""
    from src.infrastructure.external_services.meta.hashing import (
        _h,
        _normalize_mena_phone,
    )

    return _h(_normalize_mena_phone(phone)) or ""


def hash_name(name: str) -> str:
    """SHA-256 of the lowercase, punctuation-free form — Meta's `fn`/`ln` rule."""
    from src.infrastructure.external_services.meta.hashing import (
        _h,
        _normalize_meta_text,
    )

    return _h(_normalize_meta_text(name, strip_spaces=False)) or ""


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
            # HASHED. This sent the raw value, so the omnichannel path's
            # external_id could never join the storefront path's hashed one.
            from src.infrastructure.external_services.meta.hashing import _h

            digest = _h(str(external_id))
            if digest:
                user_data_hashed["external_id"] = [digest]

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
