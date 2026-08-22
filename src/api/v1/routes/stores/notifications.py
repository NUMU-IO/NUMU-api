"""Merchant notification feed routes.

URL: /stores/{store_id}/notifications

Backs the hub's bell dropdown (tabs: all / important / orders /
abandoned carts / payments / logistics) and the Notifications page.
Rows are produced by ``notification_feed_handler`` + the abandoned-cart
task; the hub renders copy from ``kind`` + ``data``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.dependencies import (
    get_merchant_notification_repository,
    get_store_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.application.services.notification_feed import SETTINGS_KEY
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


class NotificationPreferencesUpdate(BaseModel):
    muted_categories: list[Category] | None = None
    email_new_order: bool | None = None
    push_new_order: bool | None = None


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
    if body.email_new_order is not None:
        email = dict(settings.get("email_notifications") or {})
        email["new_order"] = body.email_new_order
        settings["email_notifications"] = email
    if body.push_new_order is not None:
        push = dict(settings.get("push_notifications") or {})
        push["new_order"] = body.push_new_order
        settings["push_notifications"] = push
    store.settings = settings
    await store_repo.update(store)
    return SuccessResponse(data=_prefs_from_settings(settings))
