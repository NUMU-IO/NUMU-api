"""The one rule for "this app is live on this store" (APP-STANDARD § 4.1).

An install counts only when it finished connecting (``active``) and is
enabled, the app is published, or is a draft its developer installed on one of
their own ``developer``-plan stores, and, for a Partner App, the Partner-apps
kill switch is on. A suspended app never counts.

The store payload's ``installed_apps``, the hub's session-token route and
``require_app_session`` all select through ``live_installs``, so they cannot
disagree about which apps are live.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.partner_program import partner_apps_enabled
from src.core.entities.app import AppStatus
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel


async def live_installs(db: AsyncSession, store_id: UUID) -> Select:
    """``(AppModel, AppInstallationModel)`` rows live on this store."""
    stmt = (
        select(AppModel, AppInstallationModel)
        .join(AppInstallationModel, AppModel.id == AppInstallationModel.app_id)
        .join(StoreModel, StoreModel.id == AppInstallationModel.store_id)
        .join(TenantModel, TenantModel.id == StoreModel.tenant_id)
        .where(
            AppInstallationModel.store_id == store_id,
            AppInstallationModel.status == "active",
            AppInstallationModel.is_enabled.is_(True),
            or_(
                AppModel.status == AppStatus.PUBLISHED,
                # Not yet published: only on its developer's own dev store,
                # the same rule as dev-install and OAuth consent.
                and_(
                    AppModel.status == AppStatus.DRAFT,
                    AppModel.developer_id == StoreModel.owner_id,
                    TenantModel.plan == "developer",
                ),
            ),
        )
    )
    if not await partner_apps_enabled(db):
        stmt = stmt.where(AppModel.developer_id.is_(None))
    return stmt
