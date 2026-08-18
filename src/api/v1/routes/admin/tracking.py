"""Admin platform-wide Meta tracking overview.

URL: /api/v1/admin/tracking
Requires SUPER_ADMIN role.

Why this exists: NUMU had **no** platform-level view of Meta tracking. Ops
could not answer "which stores have Meta configured", "which are erroring",
"whose token expired", or "what is our fleet-wide match quality" — and
``count_failed_in_window()`` already existed in the event-log repository,
unused. Every diagnosis started from one merchant complaining, which means
the ones who don't complain are invisible.

Read-only and aggregate: no credential material, no shopper PII, no event
payloads. Just the shape of the fleet.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.tenant.meta_event_log import MetaEventLogModel
from src.infrastructure.database.models.tenant.meta_match_quality_snapshot import (
    MetaMatchQualitySnapshotModel,
)
from src.infrastructure.database.models.tenant.store import StoreModel

router = APIRouter()


class StoreTrackingRow(BaseModel):
    """One store's Meta tracking posture."""

    store_id: str
    store_name: str | None = None
    subdomain: str | None = None

    mode: str
    pixel_count: int = 0
    has_capi_token: bool = False
    purchase_trigger: str | None = None

    events_24h: int = 0
    failures_24h: int = 0
    failure_rate_24h: float | None = None
    last_event_at: datetime | None = None

    # Outbox backlog. The failure rate above says whether this store's sends
    # are working; these say whether anything is stuck behind them. A store
    # can post a clean rate while a hundred conversions wait on the ladder.
    undelivered: int = 0
    dead_lettered: int = 0
    expired: int = 0

    worst_emq: float | None = None
    emq_captured_at: datetime | None = None


class MetaTrackingOverview(BaseModel):
    """Fleet summary + the per-store rows behind it."""

    stores_total: int
    stores_configured: int
    stores_with_events_24h: int
    stores_failing_24h: int
    stores_below_emq_threshold: int
    emq_threshold: float
    # Fleet-wide outbox backlog. The single number ops should alert on: it is
    # zero in steady state, and non-zero means events are owed to Meta right
    # now, whatever any individual store's badge says.
    events_undelivered: int = 0
    events_dead_lettered: int = 0
    rows: list[StoreTrackingRow]


def _mode_for(meta_cfg: dict) -> str:
    """Resolve display mode the same way the merchant panel does."""
    pixel_on = bool(meta_cfg.get("pixel_enabled"))
    capi_on = bool(meta_cfg.get("capi_enabled"))
    pixels = meta_cfg.get("pixels")
    if isinstance(pixels, list):
        pixel_on = pixel_on or any(
            isinstance(p, dict) and p.get("pixel_enabled") for p in pixels
        )
        capi_on = capi_on or any(
            isinstance(p, dict) and p.get("capi_enabled") for p in pixels
        )
    if pixel_on and capi_on:
        return "both"
    if capi_on:
        return "capi_only"
    if pixel_on:
        return "pixel_only"
    return "off"


@router.get(
    "/meta/overview",
    response_model=SuccessResponse[MetaTrackingOverview],
    summary="Platform-wide Meta tracking health",
    operation_id="admin_meta_tracking_overview",
)
async def meta_tracking_overview(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[object, Depends(require_admin)],
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
    only_problems: Annotated[bool, Query()] = False,
    emq_threshold: Annotated[float, Query(ge=0, le=10)] = 6.5,
):
    """Every store's Meta configuration, delivery health and match quality.

    Three aggregates in one pass rather than N+1 per store: the event counts,
    the failure counts and the latest EMQ are each one grouped query, joined in
    Python against the store list.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    stores = (await db.execute(select(StoreModel))).scalars().all()

    # Events + failures per store in the window.
    event_rows = (
        await db.execute(
            select(
                MetaEventLogModel.store_id,
                func.count().label("total"),
                func.count()
                .filter(MetaEventLogModel.response_status >= 400)
                .label("failed"),
                func.max(MetaEventLogModel.created_at).label("last_at"),
            )
            .where(MetaEventLogModel.created_at >= cutoff)
            .group_by(MetaEventLogModel.store_id)
        )
    ).all()
    events_by_store = {
        str(r.store_id): (r.total or 0, r.failed or 0, r.last_at) for r in event_rows
    }

    # Outbox state per store in the window. One grouped query for the whole
    # fleet, joined in Python like the other two aggregates.
    delivery_rows = (
        await db.execute(
            select(
                MetaEventLogModel.store_id,
                MetaEventLogModel.status,
                func.count().label("n"),
            )
            .where(MetaEventLogModel.created_at >= cutoff)
            .group_by(MetaEventLogModel.store_id, MetaEventLogModel.status)
        )
    ).all()
    delivery_by_store: dict[str, dict[str, int]] = {}
    for r in delivery_rows:
        delivery_by_store.setdefault(str(r.store_id), {})[str(r.status)] = int(r.n or 0)

    # Worst (lowest) recent EMQ per store — the number that matters is the
    # weakest event, not an average that hides it.
    emq_rows = (
        await db.execute(
            select(
                MetaMatchQualitySnapshotModel.store_id,
                func.min(MetaMatchQualitySnapshotModel.emq_score).label("worst"),
                func.max(MetaMatchQualitySnapshotModel.captured_at).label("at"),
            )
            .where(
                MetaMatchQualitySnapshotModel.captured_at
                >= datetime.now(UTC) - timedelta(days=7)
            )
            .group_by(MetaMatchQualitySnapshotModel.store_id)
        )
    ).all()
    emq_by_store = {
        str(r.store_id): (float(r.worst) if r.worst is not None else None, r.at)
        for r in emq_rows
    }

    rows: list[StoreTrackingRow] = []
    configured = failing = with_events = below_emq = 0
    fleet_undelivered = fleet_dead = 0

    for store in stores:
        meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
        mode = _mode_for(meta_cfg)
        if mode == "off":
            continue
        configured += 1

        sid = str(store.id)
        total, failed, last_at = events_by_store.get(sid, (0, 0, None))
        worst_emq, emq_at = emq_by_store.get(sid, (None, None))

        by_status = delivery_by_store.get(sid, {})
        undelivered = by_status.get("pending", 0) + by_status.get("retrying", 0)
        dead_lettered = by_status.get("dead_letter", 0)
        expired = by_status.get("expired", 0)
        fleet_undelivered += undelivered
        fleet_dead += dead_lettered

        rate = (failed / total) if total else None
        if total:
            with_events += 1
        if rate is not None and rate > 0.1:
            failing += 1
        if worst_emq is not None and worst_emq < emq_threshold:
            below_emq += 1

        pixels = meta_cfg.get("pixels")
        row = StoreTrackingRow(
            store_id=sid,
            store_name=getattr(store, "name", None),
            subdomain=getattr(store, "subdomain", None),
            mode=mode,
            pixel_count=(
                len(pixels)
                if isinstance(pixels, list) and pixels
                else (1 if meta_cfg.get("pixel_id") else 0)
            ),
            has_capi_token=bool(meta_cfg.get("capi_enabled")),
            purchase_trigger=meta_cfg.get("purchase_trigger"),
            events_24h=total,
            failures_24h=failed,
            failure_rate_24h=rate,
            last_event_at=last_at,
            undelivered=undelivered,
            dead_lettered=dead_lettered,
            expired=expired,
            worst_emq=worst_emq,
            emq_captured_at=emq_at,
        )

        if only_problems:
            # A backlog is a problem even when every settled send succeeded —
            # that is exactly the shape of an outage in progress.
            healthy = (
                (rate is None or rate <= 0.1)
                and (worst_emq is None or worst_emq >= emq_threshold)
                and not dead_lettered
            )
            if healthy and total:
                continue
        rows.append(row)

    # Worst first — the list is a work queue, not a directory.
    rows.sort(key=lambda r: r.worst_emq if r.worst_emq is not None else 99)

    return SuccessResponse(
        data=MetaTrackingOverview(
            stores_total=len(stores),
            stores_configured=configured,
            stores_with_events_24h=with_events,
            stores_failing_24h=failing,
            stores_below_emq_threshold=below_emq,
            emq_threshold=emq_threshold,
            events_undelivered=fleet_undelivered,
            events_dead_lettered=fleet_dead,
            rows=rows,
        ),
        message="Meta tracking overview",
    )
