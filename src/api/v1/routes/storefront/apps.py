"""Storefront app platform routes — Phase 6.

Customer-facing reads. Themes call these via the SDK's ``useApp(slug)``
to discover what's installed and pull app-published data.

URLs:
    GET /storefront/store/{store_id}/apps
        → list all enabled installs (sparse — slug + name + blocks).
    GET /storefront/store/{store_id}/apps/{slug}
        → single install detail + manifest + per-store settings.

The actual app *data fetching* (e.g. recommendation lists from a
third-party endpoint) is deferred — v1 ships the *resolution* layer
so theme code that branches on `useApp(...).available` works
correctly. When data fetching ships, the response gains a `data`
field and themes don't need to change.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.api.responses import SuccessResponse
from src.api.v1.routes.storefront.app_public import (
    public_manifest,
    public_settings,
    visible_installs,
)
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.public.app import AppModel

router = APIRouter()


class AppBlockSummary(BaseModel):
    type: str
    name: str
    block_schema: dict[str, Any] = {}


class InstalledAppSummary(BaseModel):
    slug: str
    name: str
    icon_url: str | None = None
    version: str
    blocks: list[AppBlockSummary] = []


class InstalledAppResponse(BaseModel):
    slug: str
    name: str
    description: str | None = None
    icon_url: str | None = None
    version: str
    manifest: dict[str, Any]
    settings: dict[str, Any]
    blocks: list[AppBlockSummary] = []
    available: bool = True


@router.get(
    "/apps",
    response_model=SuccessResponse[list[InstalledAppSummary]],
    summary="List installed apps",
    operation_id="list_storefront_apps",
)
async def list_installed_apps(store_id: UUID):
    """Return slugs of every enabled install for this store.

    Theme code uses this to know which `useApp(slug)` calls have a
    chance of returning data — the SDK can pre-populate availability
    without N round-trips.
    """

    async with AsyncSessionLocal() as session:
        stmt = await visible_installs(session, store_id)
        rows = (await session.execute(stmt)).all()

    summaries = []
    for app, _install in rows:
        blocks_raw = (app.manifest or {}).get("blocks", []) or []
        summaries.append(
            InstalledAppSummary(
                slug=app.slug,
                name=app.name,
                icon_url=app.icon_url,
                version=app.version,
                blocks=[
                    AppBlockSummary(
                        type=b.get("type", ""),
                        name=b.get("name", ""),
                        block_schema=b.get("schema", {}) or {},
                    )
                    for b in blocks_raw
                    if isinstance(b, dict)
                ],
            )
        )
    return SuccessResponse(data=summaries, message="Apps listed")


@router.get(
    "/apps/{slug}",
    response_model=SuccessResponse[InstalledAppResponse],
    summary="Get installed app detail",
    operation_id="get_storefront_app",
)
async def get_installed_app(store_id: UUID, slug: str):
    """Return manifest + per-store settings for a single install.

    404 when the slug doesn't exist OR isn't installed/enabled for
    this store. Theme code branches on this; not-installed surfaces as
    `available: false`, not as a 404 in the SDK (the SDK turns the
    HTTP 404 into a graceful no-op state).
    """

    async with AsyncSessionLocal() as session:
        stmt = (await visible_installs(session, store_id)).where(AppModel.slug == slug)
        row = (await session.execute(stmt)).one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not installed for this store.",
        )

    app, install = row
    manifest = app.manifest or {}
    blocks_raw = manifest.get("blocks", []) or []
    return SuccessResponse(
        data=InstalledAppResponse(
            slug=app.slug,
            name=app.name,
            description=app.description,
            icon_url=app.icon_url,
            version=app.version,
            # Default-deny projection. This endpoint is unauthenticated and has
            # NO host-to-store binding — `store_id` comes off the URL and is
            # never checked against the caller — so it must never echo an
            # install's raw settings (the entity docstring describes that field
            # as holding "API tokens") or the raw manifest.
            manifest=public_manifest(manifest),
            settings=public_settings(manifest, install.settings),
            blocks=[
                AppBlockSummary(
                    type=b.get("type", ""),
                    name=b.get("name", ""),
                    block_schema=b.get("schema", {}) or {},
                )
                for b in blocks_raw
                if isinstance(b, dict)
            ],
            available=True,
        ),
        message="App resolved",
    )
