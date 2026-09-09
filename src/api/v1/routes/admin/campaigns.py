"""Notifications & campaigns — what the platform sends on merchants' behalf.

URL: /api/v1/admin/campaigns — requires SUPER_ADMIN.

Two tables record outbound sends: ``marketing_campaigns`` (email and generic
channels) and ``whatsapp_campaigns``. They grew separately and have different
columns, but an operator asking "what went out, to how many people, and how
much of it failed" does not care which table it came from. This unions them
into one shape.

Read-only on purpose. Campaigns are composed and scheduled by the merchant in
their own hub; staff need to see delivery and failure, not to send on a
merchant's behalf — that would put NUMU's name on a message the merchant
never wrote.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter()

ChannelFilter = Literal["all", "whatsapp", "email", "other"]


class CampaignItem(BaseModel):
    id: str
    source: Literal["marketing", "whatsapp"]
    name: str
    channel: str
    status: str
    store_id: str | None
    store_name: str | None
    total_recipients: int
    sent_count: int
    delivered_count: int
    failed_count: int
    #: delivered / sent, as a percentage. None when nothing has been sent yet.
    delivery_rate: float | None
    scheduled_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class CampaignStats(BaseModel):
    campaigns_30d: int
    recipients_30d: int
    delivered_30d: int
    failed_30d: int
    #: Merchant-facing notifications written into the hub feed.
    notifications_30d: int
    #: Unread of those. A feed nobody reads is worth knowing about.
    notifications_unread: int


class CampaignListResponse(BaseModel):
    items: list[CampaignItem]
    total: int
    stats: CampaignStats


def _rate(delivered: int, sent: int) -> float | None:
    if not sent:
        return None
    return round(delivered / sent * 100, 1)


@router.get(
    "",
    response_model=SuccessResponse[CampaignListResponse],
    summary="List outbound campaigns across channels",
    operation_id="admin_campaigns_list",
)
async def list_campaigns(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    channel: Annotated[ChannelFilter, Query()] = "all",
    search: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    params: dict[str, object] = {"limit": limit, "offset": offset}
    filters = ["NOT (t.lifecycle_state = 'demo' OR t.is_internal IS TRUE)"]

    if search:
        filters.append("(c.name ILIKE :q OR s.name ILIKE :q)")
        params["q"] = f"%{search}%"
    if channel == "whatsapp":
        filters.append("c.source = 'whatsapp'")
    elif channel == "email":
        filters.append("c.channel = 'email'")
    elif channel == "other":
        filters.append("c.source = 'marketing' AND c.channel <> 'email'")

    clause = " AND ".join(filters)

    # The two tables are unioned before filtering so the same predicates and
    # ordering apply to both without repeating them per branch.
    union = """
        SELECT m.id, 'marketing' AS source, m.name,
               coalesce(m.channel::text, 'email') AS channel,
               m.status::text AS status, m.store_id, m.tenant_id,
               coalesce(m.total_recipients, 0) AS total_recipients,
               coalesce(m.sent_count, 0) AS sent_count,
               coalesce(m.delivered_count, 0) AS delivered_count,
               coalesce(m.failed_count, 0) AS failed_count,
               m.scheduled_at, m.started_at, m.completed_at, m.created_at
        FROM public.marketing_campaigns m
        UNION ALL
        SELECT w.id, 'whatsapp' AS source, w.name, 'whatsapp'::text AS channel,
               w.status::text AS status, w.store_id, w.tenant_id,
               coalesce(w.total_recipients, 0), coalesce(w.sent_count, 0),
               coalesce(w.delivered_count, 0), coalesce(w.failed_count, 0),
               w.scheduled_at, w.started_at, w.completed_at, w.created_at
        FROM public.whatsapp_campaigns w
    """

    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT c.*, s.name AS store_name
                    FROM ({union}) c
                    LEFT JOIN public.stores s ON s.id = c.store_id
                    LEFT JOIN public.tenants t ON t.id = c.tenant_id
                    WHERE {clause}
                    ORDER BY coalesce(c.started_at, c.scheduled_at, c.created_at) DESC
                    LIMIT :limit OFFSET :offset
                    """  # nosec B608 - interpolates module literals only; values are bound
                ),
                params,
            )
        )
        .mappings()
        .all()
    )

    total = (
        await db.execute(
            text(
                f"""
                SELECT count(*)
                FROM ({union}) c
                LEFT JOIN public.stores s ON s.id = c.store_id
                LEFT JOIN public.tenants t ON t.id = c.tenant_id
                WHERE {clause}
                """  # nosec B608 - interpolates module literals only; values are bound
            ),
            params,
        )
    ).scalar() or 0

    since = datetime.now(UTC) - timedelta(days=30)
    agg = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT count(*) AS campaigns,
                           coalesce(sum(c.total_recipients), 0) AS recipients,
                           coalesce(sum(c.delivered_count), 0) AS delivered,
                           coalesce(sum(c.failed_count), 0) AS failed
                    FROM ({union}) c
                    LEFT JOIN public.tenants t ON t.id = c.tenant_id
                    WHERE c.created_at >= :since
                      AND NOT (t.lifecycle_state = 'demo' OR t.is_internal IS TRUE)
                    """  # nosec B608 - interpolates module literals only; values are bound
                ),
                {"since": since},
            )
        )
        .mappings()
        .one()
    )

    notif = (
        (
            await db.execute(
                text(
                    """
                    SELECT count(*) AS total,
                           count(*) FILTER (WHERE n.read_at IS NULL) AS unread
                    FROM public.merchant_notifications n
                    LEFT JOIN public.tenants t ON t.id = n.tenant_id
                    WHERE n.created_at >= :since
                      AND NOT (t.lifecycle_state = 'demo' OR t.is_internal IS TRUE)
                    """
                ),
                {"since": since},
            )
        )
        .mappings()
        .one()
    )

    return SuccessResponse(
        data=CampaignListResponse(
            items=[
                CampaignItem(
                    id=str(r["id"]),
                    source=r["source"],
                    name=r["name"],
                    channel=r["channel"],
                    status=r["status"],
                    store_id=str(r["store_id"]) if r["store_id"] else None,
                    store_name=r["store_name"],
                    total_recipients=r["total_recipients"],
                    sent_count=r["sent_count"],
                    delivered_count=r["delivered_count"],
                    failed_count=r["failed_count"],
                    delivery_rate=_rate(r["delivered_count"], r["sent_count"]),
                    scheduled_at=r["scheduled_at"],
                    started_at=r["started_at"],
                    completed_at=r["completed_at"],
                    created_at=r["created_at"],
                )
                for r in rows
            ],
            total=total,
            stats=CampaignStats(
                campaigns_30d=agg["campaigns"],
                recipients_30d=agg["recipients"],
                delivered_30d=agg["delivered"],
                failed_30d=agg["failed"],
                notifications_30d=notif["total"],
                notifications_unread=notif["unread"],
            ),
        ),
        message="Campaigns retrieved successfully",
    )
