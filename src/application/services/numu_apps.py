"""NUMU Apps: NUMU's own optional features, installed like any other app.

WhatsApp and the Inbox used to be always on. Behind the per-tenant flag
``ff_numu_apps`` they follow the store's ``app_installations`` row instead:
installed and enabled means on, and uninstalling switches the feature off.

With the flag off nothing changes, so turning the flag off is the rollback.
The migration ``numu_apps_20260919`` installed both apps on every store that
already used them, so turning the flag on takes nothing away.

See docs/Plans/apps-developer-work/02-APP-MODEL.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
    AppUninstallModel,
)
from src.infrastructure.database.models.public.omnichannel import MessageThreadModel
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)

FLAG = "ff_numu_apps"

#: How long an uninstalled NUMU App's data is kept, so a reinstall restores it.
RETENTION = timedelta(days=30)

#: The NUMU Apps. While the flag is off the catalog and the install list hide
#: them, so a store sees no change until it is switched over.
NUMU_APPS = frozenset({"whatsapp", "inbox"})


async def app_enabled(db: AsyncSession, store_id: UUID, slug: str) -> bool:
    """Whether a NUMU App is on for this store: installed AND enabled.

    Always True when the store's tenant does not have ``ff_numu_apps``: the
    feature is then on, exactly as it was before it became an app. Also True
    for an unknown store, which is not this function's call to make.
    """
    row = (
        await db.execute(
            select(TenantModel.feature_flags, AppInstallationModel.is_enabled)
            .select_from(StoreModel)
            .join(TenantModel, TenantModel.id == StoreModel.tenant_id)
            .outerjoin(AppModel, AppModel.slug == slug)
            .outerjoin(
                AppInstallationModel,
                and_(
                    AppInstallationModel.store_id == StoreModel.id,
                    AppInstallationModel.app_id == AppModel.id,
                ),
            )
            .where(StoreModel.id == store_id)
        )
    ).one_or_none()
    if row is None:
        return True
    flags, enabled = row
    if not (flags or {}).get(FLAG):
        return True
    return bool(enabled)


async def schedule_purge(
    db: AsyncSession, store_id: UUID, app_id: UUID, *, now: datetime | None = None
) -> None:
    """Uninstalled: delete this app's data for the store after RETENTION."""
    purge_after = (now or datetime.now(UTC)) + RETENTION
    await db.execute(
        pg_insert(AppUninstallModel)
        .values(store_id=store_id, app_id=app_id, purge_after=purge_after)
        .on_conflict_do_update(
            constraint="uq_app_uninstall_store_app",
            set_={"purge_after": purge_after},
        )
    )


async def cancel_purge(db: AsyncSession, store_id: UUID, app_id: UUID) -> None:
    """Reinstalled inside the window: keep everything."""
    await db.execute(
        delete(AppUninstallModel).where(
            AppUninstallModel.store_id == store_id,
            AppUninstallModel.app_id == app_id,
        )
    )


async def _purge_inbox(db: AsyncSession, store_id: UUID) -> None:
    # Messages cascade from their thread.
    await db.execute(
        delete(MessageThreadModel).where(
            MessageThreadModel.store_id == store_id,
            MessageThreadModel.channel.in_(("facebook", "instagram")),
        )
    )


async def _purge_whatsapp(db: AsyncSession, store_id: UUID) -> None:
    """Customer conversations only.

    Kept on purpose: ``message_log`` (the delivery and allowance record billing
    counts against), the paid access row, and the connection, none of which
    is a customer's conversation.
    """
    from src.infrastructure.database.models.tenant.whatsapp_conversation import (
        WhatsAppConversationModel,
    )

    await db.execute(
        delete(WhatsAppConversationModel).where(
            WhatsAppConversationModel.store_id == store_id
        )
    )
    await db.execute(
        delete(MessageThreadModel).where(
            MessageThreadModel.store_id == store_id,
            MessageThreadModel.channel == "whatsapp",
        )
    )


_PURGERS = {"inbox": _purge_inbox, "whatsapp": _purge_whatsapp}


async def purge_due(db: AsyncSession, *, now: datetime | None = None) -> dict:
    """Delete the data of every uninstall past its window. Caller commits.

    A store that reinstalled has no row (cancel_purge), so nothing it uses is
    touched. An app with no purger just loses its row.
    """
    now = now or datetime.now(UTC)
    due = (
        await db.execute(
            select(AppUninstallModel.id, AppUninstallModel.store_id, AppModel.slug)
            .join(AppModel, AppModel.id == AppUninstallModel.app_id)
            .where(AppUninstallModel.purge_after <= now)
        )
    ).all()
    for row_id, store_id, slug in due:
        purger = _PURGERS.get(slug)
        if purger is not None:
            await purger(db, store_id)
        await db.execute(
            delete(AppUninstallModel).where(AppUninstallModel.id == row_id)
        )
        logger.info("numu_app_data_purged", store_id=str(store_id), app=slug)
    return {"purged": len(due)}
