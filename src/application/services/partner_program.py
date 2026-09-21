"""The Partner program switch and the partner lookups every route shares.

The program is dark until an admin opens it: ``platform_config`` key
``partner_program`` = ``{"enabled": bool}``, off when the row is missing.
While it is off every partner-facing route answers 404, so the program cannot
be discovered before the Partner Agreement exists (plan 04, Phase 2 gate).

The switch does NOT gate ``require_approved_partner`` itself: theme upload and
the marketplace developer routes sit behind that dependency, and existing
theme developers (backfilled as approved) must keep working while the
program is dark.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

CONFIG_KEY = "partner_program"

#: The agreement a partner accepts when applying. Bump it when the Partner
#: Agreement changes; partners on an older version must accept again.
AGREEMENT_VERSION = "2026-09-draft"

#: Development stores per partner (plan 06, OD-5).
MAX_DEV_STORES = 5


async def program_enabled(db: AsyncSession) -> bool:
    value = await db.scalar(
        select(PlatformConfigModel.value).where(PlatformConfigModel.key == CONFIG_KEY)
    )
    return bool((value or {}).get("enabled", False))


async def set_program_enabled(db: AsyncSession, enabled: bool) -> None:
    await db.execute(
        pg_insert(PlatformConfigModel)
        .values(key=CONFIG_KEY, value={"enabled": enabled})
        .on_conflict_do_update(
            index_elements=[PlatformConfigModel.key],
            set_={"value": {"enabled": enabled}},
        )
    )


#: The Partner-apps kill switch (platform_config), on unless turned off. Off:
#: every Partner App leaves the catalog and every storefront; NUMU Apps stay.
KILL_SWITCH_KEY = "partner_apps"


async def partner_apps_enabled(db: AsyncSession) -> bool:
    value = await db.scalar(
        select(PlatformConfigModel.value).where(
            PlatformConfigModel.key == KILL_SWITCH_KEY
        )
    )
    return bool((value or {}).get("enabled", True))


async def partner_for_user(
    db: AsyncSession, user_id: UUID
) -> PartnerAccountModel | None:
    return (
        await db.execute(
            select(PartnerAccountModel).where(PartnerAccountModel.user_id == user_id)
        )
    ).scalar_one_or_none()
