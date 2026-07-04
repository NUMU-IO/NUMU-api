"""Storefront theme-error beacon ingest.

Durable backend for the shopper-side bundle-crash beacon. The storefront
(numu-storefront) catches a theme bundle render/runtime error and POSTs a
compact report here; this route resolves the store, stamps ``tenant_id``,
and writes one ``theme_error_events`` row so crashes can later be queried
per theme version (Phase 3 moat item — see the merchant summary route).

Contract, mirroring the existing ``/track`` beacon:
  * Public — no auth (the shopper's browser is the caller).
  * Best-effort — always returns ``204``; never raises. An analytics/DB
    outage must never surface to the shopper or break the page.
  * Size-capped — oversize fields are truncated server-side before insert.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Response
from pydantic import BaseModel, Field

from src.api.dependencies.repositories import (
    get_store_repository,
    get_theme_error_event_repository,
)
from src.core.logging import get_logger
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.repositories.theme_error_event_repository import (
    ThemeErrorEventRepository,
)

logger = get_logger(__name__)
router = APIRouter()

# Storage caps. Kept as the single source of truth for both the request
# schema's hard ceilings and the defensive server-side truncation below.
_MESSAGE_MAX = 4000
_URL_MAX = 2000
_SLUG_MAX = 255
_VERSION_MAX = 50
_BUNDLE_URL_MAX = 500


class ThemeErrorReportRequest(BaseModel):
    """Shopper-side theme bundle-error report.

    Only ``message`` is required; every other field is best-effort context
    the beacon may or may not have on hand. ``max_length`` ceilings reject
    pathological payloads cheaply (422); values within the ceiling are
    truncated to the storage cap by the route.
    """

    message: str = Field(min_length=1, max_length=_MESSAGE_MAX)
    theme_slug: str | None = Field(default=None, max_length=_SLUG_MAX)
    theme_version: str | None = Field(default=None, max_length=_VERSION_MAX)
    bundle_url: str | None = Field(default=None, max_length=_BUNDLE_URL_MAX)
    url: str | None = Field(default=None, max_length=_URL_MAX)


def _cap(value: str | None, limit: int) -> str | None:
    """Truncate ``value`` to ``limit`` chars (belt-and-suspenders)."""
    if value is None:
        return None
    return value[:limit]


@router.post("/theme-error", status_code=204)
async def report_theme_error(
    body: ThemeErrorReportRequest,
    store_id: Annotated[UUID, Path()],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    theme_error_repo: Annotated[
        ThemeErrorEventRepository, Depends(get_theme_error_event_repository)
    ],
) -> Response:
    """Record one shopper-side theme bundle error. Public, fire-and-forget.

    Never raises: an invalid store, a missing tenant, or a DB blip all
    resolve to a plain ``204`` so the beacon (and the page it fired from)
    are unaffected.
    """
    try:
        store = await store_repo.get_by_id(store_id)
        if not store or not store.tenant_id:
            # Unknown store, or a store with no tenant to scope the row to.
            return Response(status_code=204)

        await theme_error_repo.create(
            store_id=store.id,
            tenant_id=store.tenant_id,
            message=body.message[:_MESSAGE_MAX],
            theme_slug=_cap(body.theme_slug, _SLUG_MAX),
            theme_version=_cap(body.theme_version, _VERSION_MAX),
            bundle_url=_cap(body.bundle_url, _BUNDLE_URL_MAX),
            url=_cap(body.url, _URL_MAX),
        )
    except Exception:
        # Analytics/telemetry outages must never break the storefront.
        logger.exception("theme_error_ingest_failed", extra={"store_id": str(store_id)})

    return Response(status_code=204)
