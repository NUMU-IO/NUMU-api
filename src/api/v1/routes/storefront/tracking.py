"""Storefront page view tracking."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, Field

from src.api.dependencies.repositories import (
    get_funnel_event_repository,
    get_page_view_repository,
    get_store_repository,
)
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)
from src.infrastructure.repositories.page_view_repository import PageViewRepository
from src.infrastructure.repositories.store_repository import StoreRepository

router = APIRouter()


_VALID_FUNNEL_STEPS = {
    "page_view",
    "product_view",
    "add_to_cart",
    "checkout_started",
    "order_completed",
    "order_delivered",
}


class TrackPageViewRequest(BaseModel):
    path: str = Field(max_length=500)
    fingerprint: str | None = Field(None, max_length=64)
    referrer: str | None = Field(None, max_length=500)
    # Optional explicit funnel step. When provided, overrides path-based inference.
    # Used by the storefront to fire add_to_cart, product_view, etc. without
    # spinning up a separate endpoint.
    step: str | None = Field(None, max_length=32)
    step_data: dict | None = None


@router.post("/track", status_code=204)
async def track_page_view(
    body: TrackPageViewRequest,
    request: Request,
    store_id: Annotated[UUID, Path()],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    pv_repo: Annotated[PageViewRepository, Depends(get_page_view_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
):
    """Record a storefront page view or funnel event. Fire-and-forget, no auth required."""
    store = await store_repo.get_by_id(store_id)
    if not store:
        return Response(status_code=204)  # Silently ignore invalid store

    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
    ua = request.headers.get("user-agent", "")[:500]

    # Resolve funnel step: explicit > path-based inference. The storefront
    # routes `/product/:id` (singular) for detail pages, so check for that
    # specifically. `/products` (plural) is the listing page → page_view.
    if body.step and body.step in _VALID_FUNNEL_STEPS:
        step = body.step
    else:
        path = body.path or ""
        is_product_detail = "/product/" in path and "/products/" not in path
        step = "product_view" if is_product_detail else "page_view"

    # Only persist a page_view row when this is actually a navigation event.
    # Pure funnel events (add_to_cart, etc.) shouldn't pollute the page_views
    # table — they have no meaningful URL path of their own.
    if step in ("page_view", "product_view"):
        await pv_repo.create(
            store_id=store.id,
            tenant_id=store.tenant_id,
            path=body.path,
            session_fingerprint=body.fingerprint,
            ip_address=ip,
            user_agent=ua,
            referrer=body.referrer,
        )

        # Update real-time counters
        try:
            from src.infrastructure.cache.realtime_counters import record_page_view

            await record_page_view(store.id, body.fingerprint, body.path)
        except Exception:
            pass

    # Emit funnel event
    try:
        step_data = body.step_data or {"path": body.path, "referrer": body.referrer}
        await funnel_repo.create(
            tenant_id=store.tenant_id,
            store_id=store.id,
            step=step,
            session_fingerprint=body.fingerprint,
            step_data=step_data,
        )
    except Exception:
        pass  # Non-critical — don't break page tracking

    return Response(status_code=204)
