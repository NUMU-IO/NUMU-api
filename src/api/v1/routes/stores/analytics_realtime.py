"""Real-time analytics routes with SSE streaming.

URL: /stores/{store_id}/analytics/realtime
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.dependencies import (
    verify_store_ownership,
    verify_store_ownership_streaming,
)
from src.api.dependencies.repositories import get_analytics_repository
from src.api.responses import SuccessResponse
from src.core.entities.store import Store
from src.core.utils.store_timezone import (
    local_day_bounds,
    resolve_store_timezone_name,
    safe_zone,
)
from src.infrastructure.cache.realtime_counters import get_snapshot
from src.infrastructure.repositories.analytics_repository import AnalyticsRepository

router = APIRouter(prefix="/{store_id}/analytics/realtime")


class RecentOrderItem(BaseModel):
    order_id: str
    order_number: str
    total: int  # cents
    customer_name: str
    item_count: int
    payment_method: str | None


class TopPageItem(BaseModel):
    path: str
    views: int


class RealtimeSnapshotResponse(BaseModel):
    views_today: int
    visitors_today: int
    active_now: int
    orders_today: int
    revenue_today: int  # cents
    recent_orders: list[RecentOrderItem]
    hourly_orders: list[int]  # 24 values, index = hour
    hourly_revenue: list[int]  # 24 values, index = hour (cents)
    top_pages: list[TopPageItem]
    # False when the counter store could not be read. Every field above is
    # then a placeholder zero, NOT a measurement — the UI must say
    # "unavailable" rather than confidently reporting no traffic.
    available: bool = True


@router.get(
    "/snapshot",
    response_model=SuccessResponse[RealtimeSnapshotResponse],
    summary="Get real-time analytics snapshot",
    operation_id="get_realtime_snapshot",
)
async def get_realtime_snapshot(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """One-time fetch of real-time analytics counters."""
    data = await get_snapshot(
        store.id, tz_name=resolve_store_timezone_name(store.settings)
    )

    recent = []
    for o in data["recent_orders"]:
        try:
            recent.append(
                RecentOrderItem(
                    order_id=o.get("order_id", ""),
                    order_number=o.get("order_number", ""),
                    total=o.get("total", 0),
                    customer_name=o.get("customer_name", ""),
                    item_count=o.get("item_count", 0),
                    payment_method=o.get("payment_method"),
                )
            )
        except Exception:
            pass

    top_pages = [
        TopPageItem(path=p["path"], views=p["views"]) for p in data.get("top_pages", [])
    ]

    return SuccessResponse(
        data=RealtimeSnapshotResponse(
            views_today=data["views_today"],
            visitors_today=data["visitors_today"],
            active_now=data["active_now"],
            orders_today=data["orders_today"],
            revenue_today=data["revenue_today"],
            recent_orders=recent,
            hourly_orders=data.get("hourly_orders", [0] * 24),
            hourly_revenue=data.get("hourly_revenue", [0] * 24),
            top_pages=top_pages,
            available=bool(data.get("available", True)),
        ),
        message="Realtime snapshot retrieved",
    )


class GeoLocationItem(BaseModel):
    location: str
    orders: int
    revenue: int  # cents
    percentage: float


class RealtimeGeoResponse(BaseModel):
    total_orders: int
    locations: list[GeoLocationItem]


@router.get(
    "/geo",
    response_model=SuccessResponse[RealtimeGeoResponse],
    summary="Today's orders by location (governorate)",
    operation_id="get_realtime_geo",
)
async def get_realtime_geo(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
):
    """Where TODAY's orders are coming from, by governorate — the geo
    layer for Live View. Sourced from order shipping addresses (real,
    merchant-entered) on the store's wall-clock day, not IP geolocation
    (page-view IPs are /24-anonymized and carry no location)."""
    tz_name = resolve_store_timezone_name(store.settings)
    today_local = datetime.now(UTC).astimezone(safe_zone(tz_name)).date()
    start, _ = local_day_bounds(today_local, tz_name)
    now = datetime.now(UTC)

    rows = await analytics_repo.sales_by_location(store.id, start, now)
    total = sum(r["orders"] for r in rows)
    locations = [
        GeoLocationItem(
            location=r["location"],
            orders=r["orders"],
            revenue=r["revenue_cents"],
            percentage=round(r["orders"] / total * 100, 1) if total > 0 else 0.0,
        )
        for r in rows
    ]
    return SuccessResponse(
        data=RealtimeGeoResponse(total_orders=total, locations=locations),
        message="Realtime geo retrieved",
    )


@router.get(
    "/stream",
    summary="SSE stream for real-time analytics",
    operation_id="get_realtime_stream",
)
async def get_realtime_stream(
    # Streaming variant: must not pin a pooled DB connection for the life
    # of the SSE connection.
    store: Annotated[Store, Depends(verify_store_ownership_streaming)],
):
    """Server-Sent Events stream pushing analytics every 5 seconds."""

    tz_name = resolve_store_timezone_name(store.settings)

    async def event_generator():
        try:
            while True:
                data = await get_snapshot(store.id, tz_name=tz_name)
                payload = json.dumps(data)
                yield f"data: {payload}\n\n"
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
