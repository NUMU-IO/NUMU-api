"""Send CAPI event use case."""

import time
from uuid import UUID

from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.infrastructure.external_services.meta import CapiClient


class SendCapiEventUseCase:
    """Use case for sending CAPI events to Meta."""

    def __init__(
        self,
        store_repository: IStoreRepository,
    ):
        self.store_repository = store_repository

    async def execute(
        self,
        store_id: UUID,
        event_name: str,
        event_id: UUID,
        event_time: int,
        user_data: dict,
        custom_data: dict | None = None,
        event_source_url: str | None = None,
    ) -> dict:
        """Send a CAPI event.

        Args:
            store_id: Store UUID
            event_name: Event name (Purchase, InitiateCheckout, etc.)
            event_id: Unique event ID
            event_time: Unix timestamp
            user_data: User data (email, phone, etc.) - will be hashed
            custom_data: Custom event data
            event_source_url: URL where event occurred

        Returns:
            API response
        """
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise ValueError("Store not found")

        pixel_id = store.settings.get("meta_pixel_id")
        capi_token = store.settings.get("meta_capi_token")

        if not pixel_id or not capi_token:
            raise ValueError("CAPI not configured for this store")

        client = CapiClient(
            pixel_id=pixel_id,
            access_token=capi_token,
        )

        try:
            result = await client.send_event(
                event_name=event_name,
                event_time=event_time,
                user_data=user_data,
                event_id=str(event_id),
                custom_data=custom_data,
                event_source_url=event_source_url,
            )

            return result

        except Exception as e:
            raise RuntimeError(f"CAPI request failed: {e}")
        finally:
            await client.close()

    async def send_purchase(
        self,
        store_id: UUID,
        value: int,
        currency: str,
        user_data: dict,
        order_id: str | None = None,
    ) -> dict:
        """Send a Purchase event."""
        import uuid

        event_id = uuid.uuid4()
        event_time = int(time.time())

        custom_data = {
            "value": value,
            "currency": currency,
        }
        if order_id:
            custom_data["order_id"] = order_id

        return await self.execute(
            store_id=store_id,
            event_name="Purchase",
            event_id=event_id,
            event_time=event_time,
            user_data=user_data,
            custom_data=custom_data,
        )

    async def send_initiate_checkout(
        self,
        store_id: UUID,
        value: int,
        currency: str,
        user_data: dict,
        content_ids: list[str] | None = None,
    ) -> dict:
        """Send an InitiateCheckout event."""
        import uuid

        event_id = uuid.uuid4()
        event_time = int(time.time())

        custom_data = {
            "value": value,
            "currency": currency,
        }
        if content_ids:
            custom_data["content_ids"] = content_ids

        return await self.execute(
            store_id=store_id,
            event_name="InitiateCheckout",
            event_id=event_id,
            event_time=event_time,
            user_data=user_data,
            custom_data=custom_data,
        )
