"""Disconnect a merchant's BYO Meta WhatsApp connection (FR-023).

Marks the ``ServiceCredential`` row inactive and reverts the store to
``platform_managed`` mode. Restores the prior platform-managed
notification toggle snapshot if one exists; otherwise defaults all
toggles to True (FR-019a).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.configuration import (
    ServiceCredential,
    ServiceName,
    ServiceType,
)
from src.infrastructure.database.models.tenant.store import StoreModel


class DisconnectBYOCredentialsUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute(self, *, store_id: UUID) -> dict[str, Any]:
        store = (
            await self.session.execute(
                select(StoreModel).where(StoreModel.id == store_id)
            )
        ).scalar_one_or_none()
        if store is None:
            raise ValueError(f"Store {store_id} not found.")

        # Deactivate the BYO credential row. We do not DELETE — keeping
        # the row around with is_active=False preserves any audit trail
        # of when the credentials were active.
        cred = (
            await self.session.execute(
                select(ServiceCredential).where(
                    ServiceCredential.tenant_id == store.tenant_id,
                    ServiceCredential.service_type == ServiceType.WHATSAPP,
                    ServiceCredential.service_name == ServiceName.WHATSAPP_BUSINESS,
                )
            )
        ).scalar_one_or_none()
        if cred is not None and cred.is_active:
            cred.is_active = False
            cred.is_validated = False

        # Restore toggles. The connect use-case snapshots the prior
        # platform-managed state to ``whatsapp_notifications_prev_platform_managed``;
        # we restore from there if present, otherwise default all-True
        # (platform-managed safe default per FR-019a).
        store_settings = dict(store.settings or {})
        prev = store_settings.get("whatsapp_notifications_prev_platform_managed")
        if isinstance(prev, dict) and prev:
            store_settings["whatsapp_notifications"] = dict(prev)
            # Clear the snapshot — a future BYO connect will re-snapshot
            # from the current state.
            store_settings.pop("whatsapp_notifications_prev_platform_managed", None)
        else:
            store_settings["whatsapp_notifications"] = {
                "order_confirmation": True,
                "payment_received": True,
                "shipping_update": True,
                "delivery_confirmation": True,
                "abandoned_cart": True,
                "marketing": False,
            }

        # Strip BYO connection metadata, keep platform-managed enabled.
        wa_settings = dict(store_settings.get("whatsapp") or {})
        wa_settings["connection_type"] = "shared"
        wa_settings["is_configured"] = False
        wa_settings.pop("credential_error", None)
        wa_settings.pop("last_configured", None)
        store_settings["whatsapp"] = wa_settings

        store.settings = store_settings
        await self.session.flush()

        return {
            "mode": "platform_managed",
            "connected": True,  # platform creds are always considered "connected"
            "phone_display_name": None,
            "display_phone_number": None,
            "waba_id": None,
            "last_validated_at": datetime.now(UTC),
            "credential_error": None,
            "notifications": store_settings["whatsapp_notifications"],
        }
