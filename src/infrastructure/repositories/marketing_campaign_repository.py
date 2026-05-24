"""Marketing campaign repository — Phase 8.6."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.marketing_campaign import (
    CampaignChannel,
    CampaignStatus,
    MarketingCampaign,
)
from src.infrastructure.database.models.tenant.marketing_campaign import (
    MarketingCampaignModel,
)


def _to_entity(row: MarketingCampaignModel) -> MarketingCampaign:
    return MarketingCampaign(
        id=row.id,
        tenant_id=row.tenant_id,
        store_id=row.store_id,
        channel=CampaignChannel(row.channel)
        if isinstance(row.channel, str)
        else row.channel,
        name=row.name,
        status=CampaignStatus(row.status)
        if isinstance(row.status, str)
        else row.status,
        template_id=row.template_id,
        inline_subject=row.inline_subject,
        inline_body=row.inline_body,
        segment_id=row.segment_id,
        audience_filter=row.audience_filter,
        scheduled_at=row.scheduled_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        canceled_at=row.canceled_at,
        total_recipients=row.total_recipients,
        sent_count=row.sent_count,
        delivered_count=row.delivered_count,
        failed_count=row.failed_count,
        note=row.note,
        promoted_item=row.promoted_item,
        created_by=row.created_by,
        short_code=row.short_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class MarketingCampaignRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, campaign_id: UUID) -> MarketingCampaign | None:
        row = (
            await self._session.execute(
                select(MarketingCampaignModel).where(
                    MarketingCampaignModel.id == campaign_id
                )
            )
        ).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list_for_store(
        self,
        store_id: UUID,
        *,
        status: CampaignStatus | None = None,
        channel: CampaignChannel | None = None,
    ) -> list[MarketingCampaign]:
        stmt = select(MarketingCampaignModel).where(
            MarketingCampaignModel.store_id == store_id
        )
        if status is not None:
            stmt = stmt.where(MarketingCampaignModel.status == status)
        if channel is not None:
            stmt = stmt.where(MarketingCampaignModel.channel == channel)
        stmt = stmt.order_by(MarketingCampaignModel.created_at.desc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(r) for r in rows]

    async def list_due(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> list[MarketingCampaign]:
        """Campaigns ready to send: status=SCHEDULED and
        scheduled_at <= now. Used by the Celery sweep task."""
        ts = now or datetime.now(UTC)
        rows = (
            (
                await self._session.execute(
                    select(MarketingCampaignModel)
                    .where(
                        and_(
                            MarketingCampaignModel.status == CampaignStatus.SCHEDULED,
                            MarketingCampaignModel.scheduled_at.is_not(None),
                            MarketingCampaignModel.scheduled_at <= ts,
                        )
                    )
                    .order_by(MarketingCampaignModel.scheduled_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [_to_entity(r) for r in rows]

    async def create(self, campaign: MarketingCampaign) -> MarketingCampaign:
        row = MarketingCampaignModel(
            tenant_id=campaign.tenant_id,
            store_id=campaign.store_id,
            channel=campaign.channel,
            name=campaign.name,
            status=campaign.status,
            template_id=campaign.template_id,
            inline_subject=campaign.inline_subject,
            inline_body=campaign.inline_body,
            segment_id=campaign.segment_id,
            audience_filter=campaign.audience_filter,
            scheduled_at=campaign.scheduled_at,
            note=campaign.note,
            promoted_item=campaign.promoted_item,
            created_by=campaign.created_by,
            short_code=campaign.short_code,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _to_entity(row)

    async def transition(
        self,
        campaign_id: UUID,
        target: CampaignStatus,
        *,
        scheduled_at: datetime | None = None,
    ) -> MarketingCampaign:
        """Move a campaign through its state machine + stamp the
        relevant timestamp. The service layer validates the
        transition via `MarketingCampaign.can_transition_to`; this
        repo just persists.
        """
        row = (
            await self._session.execute(
                select(MarketingCampaignModel)
                .where(MarketingCampaignModel.id == campaign_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Campaign {campaign_id} not found")
        row.status = target
        now = datetime.now(UTC)
        if target == CampaignStatus.SCHEDULED:
            row.scheduled_at = scheduled_at or row.scheduled_at
        elif target == CampaignStatus.SENDING:
            row.started_at = now
        elif target == CampaignStatus.COMPLETED:
            row.completed_at = now
        elif target == CampaignStatus.CANCELED:
            row.canceled_at = now
        elif target == CampaignStatus.DRAFT:
            # Re-drafting clears the schedule.
            row.scheduled_at = None
        await self._session.flush()
        await self._session.refresh(row)
        return _to_entity(row)

    async def update_counters(
        self,
        campaign_id: UUID,
        *,
        total_recipients: int | None = None,
        sent_delta: int = 0,
        delivered_delta: int = 0,
        failed_delta: int = 0,
    ) -> MarketingCampaign:
        """Counter increments for the sweep — atomic per row via
        SELECT FOR UPDATE so two workers can't lose updates."""
        row = (
            await self._session.execute(
                select(MarketingCampaignModel)
                .where(MarketingCampaignModel.id == campaign_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Campaign {campaign_id} not found")
        if total_recipients is not None:
            row.total_recipients = total_recipients
        if sent_delta:
            row.sent_count += sent_delta
        if delivered_delta:
            row.delivered_count += delivered_delta
        if failed_delta:
            row.failed_count += failed_delta
        await self._session.flush()
        await self._session.refresh(row)
        return _to_entity(row)
