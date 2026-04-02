"""Storefront page view tracking."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, Field

from src.api.dependencies.repositories import (
    get_page_view_repository,
    get_store_repository,
)
from src.infrastructure.repositories.page_view_repository import PageViewRepository
from src.infrastructure.repositories.store_repository import StoreRepository

router = APIRouter()


class TrackPageViewRequest(BaseModel):
    path: str = Field(max_length=500)
    fingerprint: str | None = Field(None, max_length=64)
    referrer: str | None = Field(None, max_length=500)


@router.post("/track", status_code=204)
async def track_page_view(
    body: TrackPageViewRequest,
    request: Request,
    store_id: Annotated[UUID, Path()],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    pv_repo: Annotated[PageViewRepository, Depends(get_page_view_repository)],
):
    """Record a storefront page view. Fire-and-forget, no auth required."""
    store = await store_repo.get_by_id(store_id)
    if not store:
        return Response(status_code=204)  # Silently ignore invalid store

    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
    ua = request.headers.get("user-agent", "")[:500]

    await pv_repo.create(
        store_id=store.id,
        tenant_id=store.tenant_id,
        path=body.path,
        session_fingerprint=body.fingerprint,
        ip_address=ip,
        user_agent=ua,
        referrer=body.referrer,
    )
    return Response(status_code=204)
