"""Merchant notification feed routes.

URL: /stores/{store_id}/notifications

Backs the hub's bell dropdown (tabs: all / important / orders /
abandoned carts / payments / logistics) and the Notifications page.
Rows are produced by ``notification_feed_handler`` + the abandoned-cart
task; the hub renders copy from ``kind`` + ``data``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.dependencies import (
    get_merchant_notification_repository,
    get_store_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.application.services.notification_feed import (
    SETTINGS_KEY,
    email_important_enabled,
    notification_channel,
    push_important_enabled,
    push_rich_details_enabled,
)
from src.config import settings as app_settings
from src.core.entities.store import Store
from src.infrastructure.database.models.tenant.merchant_notification import (
    NOTIFICATION_CATEGORIES,
    MerchantNotificationModel,
)
from src.infrastructure.repositories import (
    MerchantNotificationRepository,
    StoreRepository,
)

router = APIRouter(prefix="/{store_id}/notifications")

Category = Literal["orders", "abandoned_carts", "payments", "logistics", "system"]


class NotificationItemResponse(BaseModel):
    id: UUID
    category: str
    kind: str
    data: dict[str, Any]
    link: str | None
    entity_type: str | None
    entity_id: UUID | None
    is_important: bool
    is_read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationItemResponse]
    next_cursor: str | None


class UnreadCountsResponse(BaseModel):
    total: int
    important: int
    by_category: dict[str, int]


class MarkReadRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=200)


class MarkAllReadRequest(BaseModel):
    category: Category | None = None


class MarkReadResponse(BaseModel):
    updated: int


class NotificationPreferencesResponse(BaseModel):
    """Feed categories the merchant has muted + the two existing
    new-order channel toggles (email / push) so one page owns them all."""

    muted_categories: list[str]
    email_new_order: bool
    push_new_order: bool
    # Web-push for important feed rows (cancelled / payment failed /
    # returned / kill-switch). Opt-out; default on.
    push_important: bool
    # Customer name / items / payment method in push bodies (like the
    # new-order email). Opt-out; default on.
    push_rich_details: bool
    # Urgent alerts by email too (reaches phones without the installed PWA).
    email_important: bool


class NotificationPreferencesUpdate(BaseModel):
    muted_categories: list[Category] | None = None
    email_new_order: bool | None = None
    push_new_order: bool | None = None
    push_important: bool | None = None
    push_rich_details: bool | None = None
    email_important: bool | None = None


def _to_response(m: MerchantNotificationModel) -> NotificationItemResponse:
    return NotificationItemResponse(
        id=m.id,
        category=m.category,
        kind=m.kind,
        data=m.data or {},
        link=m.link,
        entity_type=m.entity_type,
        entity_id=m.entity_id,
        is_important=bool(m.is_important),
        is_read=m.read_at is not None,
        created_at=m.created_at,
    )


@router.get(
    "/",
    response_model=SuccessResponse[NotificationListResponse],
    summary="List merchant notifications (newest first, keyset paginated)",
    operation_id="list_merchant_notifications",
)
async def list_notifications(
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        MerchantNotificationRepository, Depends(get_merchant_notification_repository)
    ],
    category: Category | None = Query(None),
    important: bool = Query(False, description="Only important notifications."),
    unread_only: bool = Query(False),
    cursor: str | None = Query(None, description="`next_cursor` from a prior page."),
    limit: int = Query(20, ge=1, le=100),
):
    rows, next_cursor = await repo.list_for_store(
        store.id,
        category=category,
        important_only=important,
        unread_only=unread_only,
        cursor=cursor,
        limit=limit,
    )
    return SuccessResponse(
        data=NotificationListResponse(
            items=[_to_response(r) for r in rows], next_cursor=next_cursor
        )
    )


@router.get(
    "/unread-count",
    response_model=SuccessResponse[UnreadCountsResponse],
    summary="Unread notification counts (bell badge + tab badges)",
    operation_id="get_merchant_notification_unread_count",
)
async def unread_count(
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        MerchantNotificationRepository, Depends(get_merchant_notification_repository)
    ],
):
    counts = await repo.unread_counts(store.id)
    return SuccessResponse(data=UnreadCountsResponse(**counts))


@router.post(
    "/read",
    response_model=SuccessResponse[MarkReadResponse],
    summary="Mark specific notifications read",
    operation_id="mark_merchant_notifications_read",
)
async def mark_read(
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        MerchantNotificationRepository, Depends(get_merchant_notification_repository)
    ],
    body: MarkReadRequest,
):
    updated = await repo.mark_read(store.id, body.ids)
    await repo.session.commit()
    return SuccessResponse(data=MarkReadResponse(updated=updated))


@router.post(
    "/read-all",
    response_model=SuccessResponse[MarkReadResponse],
    summary="Mark all (optionally one category) notifications read",
    operation_id="mark_all_merchant_notifications_read",
)
async def mark_all_read(
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        MerchantNotificationRepository, Depends(get_merchant_notification_repository)
    ],
    body: MarkAllReadRequest | None = None,
):
    updated = await repo.mark_all_read(
        store.id, category=body.category if body else None
    )
    await repo.session.commit()
    return SuccessResponse(data=MarkReadResponse(updated=updated))


STREAM_HEARTBEAT_S = 25.0
STREAM_FALLBACK_POLL_S = 20.0
# SSE framing: one `data:` line per event, blank line terminates the frame;
# a comment line (`: ping`) keeps proxies from idling the connection out.
SSE_PING = ": ping" + "\n" * 2


def sse_frame(payload: dict) -> str:
    return "data: " + json.dumps(payload) + "\n" * 2


@router.get(
    "/stream",
    summary="SSE stream: one event per new notification (plus heartbeats)",
    operation_id="stream_merchant_notifications",
)
async def stream_notifications(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """Server-Sent Events.

    Relays the Redis channel ``store:{id}:notifications`` that
    ``notification_feed.fanout`` publishes to after each commit; the hub
    invalidates its queries on every frame. Without Redis the stream
    degrades to a 20 s tick so the client still refetches.
    """
    store_id = store.id

    async def event_generator():
        pubsub = None
        publisher = None
        try:
            if app_settings.redis_host:
                try:
                    from src.infrastructure.realtime.redis_pubsub import (
                        RealtimePublisher,
                    )

                    publisher = RealtimePublisher()
                    pubsub = await publisher.subscribe(notification_channel(store_id))
                except Exception:  # noqa: BLE001 — fall back to ticking
                    pubsub = None
            yield sse_frame({"type": "connected"})
            while True:
                if pubsub is not None:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=STREAM_HEARTBEAT_S
                    )
                    if message:
                        raw = message.get("data")
                        if isinstance(raw, bytes | bytearray):
                            raw = raw.decode("utf-8")
                        try:
                            yield sse_frame(json.loads(raw))
                        except (ValueError, TypeError):
                            continue
                    else:
                        yield SSE_PING
                else:
                    await asyncio.sleep(STREAM_FALLBACK_POLL_S)
                    yield sse_frame({"type": "tick"})
        except asyncio.CancelledError:
            return
        finally:
            if publisher is not None and pubsub is not None:
                try:
                    await publisher.unsubscribe(notification_channel(store_id), pubsub)
                    await publisher.redis.aclose()
                except Exception:  # noqa: BLE001
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _prefs_from_settings(settings: dict | None) -> NotificationPreferencesResponse:
    s = settings or {}
    center = s.get(SETTINGS_KEY) or {}
    email = (s.get("email_notifications") or {}).get("new_order", True)
    push = (s.get("push_notifications") or {}).get("new_order", True)
    return NotificationPreferencesResponse(
        muted_categories=[
            c
            for c in (center.get("muted_categories") or [])
            if c in NOTIFICATION_CATEGORIES
        ],
        email_new_order=bool(email),
        push_new_order=bool(push),
        push_important=push_important_enabled(s),
        push_rich_details=push_rich_details_enabled(s),
        email_important=email_important_enabled(s),
    )


@router.get(
    "/preferences",
    response_model=SuccessResponse[NotificationPreferencesResponse],
    summary="Notification preferences",
    operation_id="get_merchant_notification_preferences",
)
async def get_preferences(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    return SuccessResponse(data=_prefs_from_settings(store.settings))


@router.put(
    "/preferences",
    response_model=SuccessResponse[NotificationPreferencesResponse],
    summary="Update notification preferences",
    operation_id="update_merchant_notification_preferences",
)
async def update_preferences(
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    body: NotificationPreferencesUpdate,
):
    if body.model_dump(exclude_none=True) == {}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No preference fields supplied.",
        )
    settings = dict(store.settings or {})
    if body.muted_categories is not None:
        center = dict(settings.get(SETTINGS_KEY) or {})
        center["muted_categories"] = sorted(set(body.muted_categories))
        settings[SETTINGS_KEY] = center
    if body.email_new_order is not None or body.email_important is not None:
        email = dict(settings.get("email_notifications") or {})
        if body.email_new_order is not None:
            email["new_order"] = body.email_new_order
        if body.email_important is not None:
            email["important"] = body.email_important
        settings["email_notifications"] = email
    if (
        body.push_new_order is not None
        or body.push_important is not None
        or body.push_rich_details is not None
    ):
        push = dict(settings.get("push_notifications") or {})
        if body.push_new_order is not None:
            push["new_order"] = body.push_new_order
        if body.push_important is not None:
            push["important"] = body.push_important
        if body.push_rich_details is not None:
            push["rich_details"] = body.push_rich_details
        settings["push_notifications"] = push
    store.settings = settings
    await store_repo.update(store)
    return SuccessResponse(data=_prefs_from_settings(settings))
