"""Repository for the per-store GOWA device registry."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.whatsapp_gowa_device import (
    WhatsAppGowaDeviceModel,
)

__all__ = ["WhatsAppGowaDeviceRepository"]


class WhatsAppGowaDeviceRepository:
    """Reads and writes the store <-> GOWA device mapping."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_for_store(
        self, store_id: UUID
    ) -> WhatsAppGowaDeviceModel | None:
        """The store's live device, or None when it has never paired.

        Called on the outbound path for every message a GOWA store sends, so it
        stays a single indexed lookup.
        """
        result = await self.session.execute(
            select(WhatsAppGowaDeviceModel)
            .where(
                WhatsAppGowaDeviceModel.store_id == store_id,
                WhatsAppGowaDeviceModel.is_active.is_(True),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_platform_device(self) -> WhatsAppGowaDeviceModel | None:
        """The shared NUMU number, used by every store without its own.

        The GOWA equivalent of the platform Meta credentials: one account
        sending for the whole fleet, belonging to no single store.
        """
        result = await self.session.execute(
            select(WhatsAppGowaDeviceModel)
            .where(
                WhatsAppGowaDeviceModel.is_platform.is_(True),
                WhatsAppGowaDeviceModel.is_active.is_(True),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_platform_device(
        self, device_id: str, acknowledged_by: UUID | None = None
    ) -> WhatsAppGowaDeviceModel:
        """Register (or re-register) the shared platform device."""
        existing = await self.get_platform_device()
        if existing:
            existing.is_active = False
            existing.status = "logged_out"
            await self.session.flush()
        device = WhatsAppGowaDeviceModel(
            tenant_id=None,
            store_id=None,
            is_platform=True,
            device_id=device_id,
            status="pending",
            is_active=True,
            consent_acknowledged_at=datetime.now(UTC) if acknowledged_by else None,
            consent_acknowledged_by=acknowledged_by,
        )
        self.session.add(device)
        await self.session.flush()
        return device

    async def get_by_device_id(self, device_id: str) -> WhatsAppGowaDeviceModel | None:
        """Reverse lookup for inbound webhooks, which are keyed by device."""
        result = await self.session.execute(
            select(WhatsAppGowaDeviceModel)
            .where(
                WhatsAppGowaDeviceModel.device_id == device_id,
                WhatsAppGowaDeviceModel.is_active.is_(True),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_pending(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        device_id: str,
        acknowledged_by: UUID | None = None,
    ) -> WhatsAppGowaDeviceModel:
        """Register a device slot, before the QR has been scanned.

        Deactivates any existing active row first. The partial unique index
        allows only one active row per store, so re-pairing without this would
        raise instead of replacing — and a merchant re-pairing after a dropped
        session is the normal case, not an error.
        """
        await self.deactivate_for_store(store_id)
        device = WhatsAppGowaDeviceModel(
            tenant_id=tenant_id,
            store_id=store_id,
            device_id=device_id,
            status="pending",
            is_active=True,
            consent_acknowledged_at=datetime.now(UTC) if acknowledged_by else None,
            consent_acknowledged_by=acknowledged_by,
        )
        self.session.add(device)
        await self.session.flush()
        return device

    async def mark_connected(self, device_id: str, phone: str | None = None) -> None:
        """Pairing completed — record the number that is now live."""
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "status": "connected",
            "last_seen_at": now,
            "paired_at": now,
            "last_error": None,
        }
        if phone:
            values["phone"] = phone
        await self.session.execute(
            update(WhatsAppGowaDeviceModel)
            .where(
                WhatsAppGowaDeviceModel.device_id == device_id,
                WhatsAppGowaDeviceModel.is_active.is_(True),
            )
            .values(**values)
        )

    async def mark_status(
        self, device_id: str, status: str, error: str | None = None
    ) -> None:
        """Record a lifecycle change reported by GOWA.

        ``logged_out`` is the one that matters operationally: on an unofficial
        transport it is how a ban presents, and the store stops being able to
        send until someone re-pairs. ``last_error`` carries GOWA's own wording
        because a ban and an ordinary disconnect are otherwise identical here.
        """
        await self.session.execute(
            update(WhatsAppGowaDeviceModel)
            .where(
                WhatsAppGowaDeviceModel.device_id == device_id,
                WhatsAppGowaDeviceModel.is_active.is_(True),
            )
            .values(
                status=status,
                last_error=error,
                last_seen_at=datetime.now(UTC),
            )
        )

    async def touch(self, device_id: str) -> None:
        """Bump ``last_seen_at``; staleness is how a silent death is spotted."""
        await self.session.execute(
            update(WhatsAppGowaDeviceModel)
            .where(
                WhatsAppGowaDeviceModel.device_id == device_id,
                WhatsAppGowaDeviceModel.is_active.is_(True),
            )
            .values(last_seen_at=datetime.now(UTC))
        )

    async def deactivate_for_store(self, store_id: UUID) -> None:
        """Unpair: retire the store's active row, keeping it for the audit trail."""
        await self.session.execute(
            update(WhatsAppGowaDeviceModel)
            .where(
                WhatsAppGowaDeviceModel.store_id == store_id,
                WhatsAppGowaDeviceModel.is_active.is_(True),
            )
            .values(is_active=False, status="logged_out")
        )
