"""Repository for campaign_activities — feature 002 US5.

Audit + status log for merchant-initiated campaign actions. Today
captures only ``backfill_attribution``; extensible via the ``type``
column.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.campaign_activity import (
    CampaignActivityModel,
)


class CampaignActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(CampaignActivityModel.tenant_id == tid)
        return query

    async def create(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        campaign_id: UUID,
        type_: str,
        payload: dict[str, Any],
        run_by: UUID,
    ) -> CampaignActivityModel:
        activity = CampaignActivityModel(
            tenant_id=tenant_id,
            store_id=store_id,
            campaign_id=campaign_id,
            type=type_,
            status="running",
            payload=payload,
            run_by=run_by,
        )
        self.session.add(activity)
        await self.session.flush()
        return activity

    async def update_status(
        self,
        activity_id: UUID,
        *,
        status: str,
        affected_count: int | None = None,
        skipped_count: int | None = None,
        error_message: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if status in ("completed", "failed"):
            values["completed_at"] = datetime.now(UTC)
        if affected_count is not None:
            values["affected_count"] = affected_count
        if skipped_count is not None:
            values["skipped_count"] = skipped_count
        if error_message is not None:
            values["error_message"] = error_message
        stmt = (
            update(CampaignActivityModel)
            .where(CampaignActivityModel.id == activity_id)
            .values(**values)
        )
        await self.session.execute(self._tenant_filter(stmt))
        await self.session.flush()

    async def list_for_campaign(
        self,
        campaign_id: UUID,
        limit: int = 20,
        type_: str | None = None,
    ) -> list[CampaignActivityModel]:
        q = (
            select(CampaignActivityModel)
            .where(CampaignActivityModel.campaign_id == campaign_id)
            .order_by(desc(CampaignActivityModel.run_at))
            .limit(limit)
        )
        if type_:
            q = q.where(CampaignActivityModel.type == type_)
        q = self._tenant_filter(q)
        return list((await self.session.execute(q)).scalars().all())

    async def get_running(
        self,
        campaign_id: UUID,
        type_: str,
    ) -> CampaignActivityModel | None:
        """Returns a running activity of the given type for the campaign,
        or None. Used by the POST handler for the 409 concurrent-backfill
        guard.
        """
        q = (
            select(CampaignActivityModel)
            .where(
                CampaignActivityModel.campaign_id == campaign_id,
                CampaignActivityModel.type == type_,
                CampaignActivityModel.status == "running",
            )
            .limit(1)
        )
        q = self._tenant_filter(q)
        return (await self.session.execute(q)).scalar_one_or_none()
