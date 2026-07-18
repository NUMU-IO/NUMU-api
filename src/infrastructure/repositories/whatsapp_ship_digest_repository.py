"""Repository for COD Autopilot ship-digest rows (004-cod-autopilot)."""

import re
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.whatsapp_ship_digest import (
    WhatsAppShipDigestModel,
)


def _digits(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")


class WhatsAppShipDigestRepository:
    """Data access for ``whatsapp_ship_digests``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, digest_id: UUID) -> WhatsAppShipDigestModel | None:
        result = await self.session.execute(
            select(WhatsAppShipDigestModel).where(
                WhatsAppShipDigestModel.id == digest_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_store_date(
        self, store_id: UUID, digest_date: date
    ) -> WhatsAppShipDigestModel | None:
        result = await self.session.execute(
            select(WhatsAppShipDigestModel).where(
                WhatsAppShipDigestModel.store_id == store_id,
                WhatsAppShipDigestModel.digest_date == digest_date,
            )
        )
        return result.scalar_one_or_none()

    async def get_open_for_phone(
        self, from_phone: str, now: datetime
    ) -> WhatsAppShipDigestModel | None:
        """Resolve a merchant's inbound free-text reply to its open digest.

        "Open" = unprocessed and unexpired. Phone matching is digits-based
        (Meta sends country-code digits without ``+``), falling back to the
        last 9 digits like ``_phones_match`` in order_confirmation_service.
        Newest first so a stale unexpired digest never shadows today's.
        """
        wanted = _digits(from_phone)
        if not wanted:
            return None
        result = await self.session.execute(
            select(WhatsAppShipDigestModel)
            .where(
                WhatsAppShipDigestModel.processed_at.is_(None),
                WhatsAppShipDigestModel.expires_at > now,
            )
            .order_by(WhatsAppShipDigestModel.sent_at.desc())
            .limit(50)
        )
        for row in result.scalars().all():
            have = _digits(row.merchant_phone)
            if have and (have == wanted or have[-9:] == wanted[-9:]):
                return row
        return None

    async def create(self, model: WhatsAppShipDigestModel) -> WhatsAppShipDigestModel:
        self.session.add(model)
        await self.session.flush()
        return model

    async def mark_processed(
        self,
        digest: WhatsAppShipDigestModel,
        *,
        response_type: str,
        response_raw: str | None,
        excepted_numbers: list[int] | None,
    ) -> None:
        """Consume the digest exactly once (FR-008). Caller must have checked
        ``processed_at is None`` before acting on the orders."""
        digest.response_type = response_type
        digest.response_raw = response_raw
        digest.excepted_numbers = excepted_numbers
        digest.processed_at = datetime.now(UTC)
        await self.session.flush()
