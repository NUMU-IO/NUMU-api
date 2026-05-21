"""Real-time analytics routes with SSE streaming.

URL: /stores/{store_id}/analytics/realtime
"""

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.dependencies import verify_store_ownership
from src.api.responses import SuccessResponse
from src.core.entities.store import Store
from src.infrastructure.cache.realtime_counters import get_snapshot

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
    data = await get_snapshot(store.id)

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
        ),
        message="Realtime snapshot retrieved",
    )


@router.get(
    "/stream",
    summary="SSE stream for real-time analytics",
    operation_id="get_realtime_stream",
)
async def get_realtime_stream(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """Server-Sent Events stream pushing analytics every 5 seconds."""

    async def event_generator():
        try:
            while True:
                data = await get_snapshot(store.id)
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
