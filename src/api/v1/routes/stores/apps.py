"""Merchant app marketplace routes — Phase 6.

Mounted at /stores/{store_id}/apps/

Endpoints:
  GET    /catalog              — list all published apps the merchant
                                 can install (global registry).
  GET    /                     — list this store's installs.
  POST   /{slug}/install       — install + activate an app on this store.
  PUT    /{slug}/settings      — update per-store settings.
  POST   /{slug}/disable       — soft-disable (keeps settings).
  POST   /{slug}/enable        — re-enable.
  DELETE /{slug}               — uninstall (deletes settings).

Apps themselves are managed by their developers in the marketplace
admin (out of scope for v1 — admins seed via SQL/console). Merchants
only ever see *published* apps in the catalog.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.core.entities.app import AppStatus
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)
from src.infrastructure.repositories import StoreRepository

router = APIRouter(
    prefix="/{store_id}/apps",
    tags=["Apps"],
    dependencies=[Depends(verify_store_ownership)],
)


# ─── Schemas ───────────────────────────────────────────────────────


class AppCatalogEntry(BaseModel):
    slug: str
    name: str
    description: str | None = None
    icon_url: str | None = None
    version: str
    blocks: list[dict[str, Any]] = []


class AppInstallation(BaseModel):
    slug: str
    name: str
    description: str | None = None
    icon_url: str | None = None
    version: str
    is_enabled: bool
    settings: dict[str, Any]
    blocks: list[dict[str, Any]] = []


class UpdateSettingsRequest(BaseModel):
    settings: dict[str, Any]


# ─── Catalog ───────────────────────────────────────────────────────


@router.get(
    "/catalog",
    response_model=SuccessResponse[list[AppCatalogEntry]],
    summary="List installable apps",
    operation_id="list_app_catalog",
)
async def list_catalog():
    async with AsyncSessionLocal() as session:
        stmt = select(AppModel).where(AppModel.status == AppStatus.PUBLISHED)
        rows = (await session.execute(stmt)).scalars().all()
    return SuccessResponse(
        data=[
            AppCatalogEntry(
                slug=a.slug,
                name=a.name,
                description=a.description,
                icon_url=a.icon_url,
                version=a.version,
                blocks=(a.manifest or {}).get("blocks", []) or [],
            )
            for a in rows
        ],
        message="Catalog listed",
    )


# ─── Installations ─────────────────────────────────────────────────


@router.get(
    "",
    response_model=SuccessResponse[list[AppInstallation]],
    summary="List installs for this store",
    operation_id="list_app_installations",
)
async def list_installations(store_id: UUID):
    async with AsyncSessionLocal() as session:
        stmt = (
            select(AppModel, AppInstallationModel)
            .join(AppInstallationModel, AppModel.id == AppInstallationModel.app_id)
            .where(AppInstallationModel.store_id == store_id)
        )
        rows = (await session.execute(stmt)).all()
    return SuccessResponse(
        data=[
            AppInstallation(
                slug=app.slug,
                name=app.name,
                description=app.description,
                icon_url=app.icon_url,
                version=app.version,
                is_enabled=install.is_enabled,
                settings=install.settings or {},
                blocks=(app.manifest or {}).get("blocks", []) or [],
            )
            for app, install in rows
        ],
        message="Installations listed",
    )


@router.post(
    "/{slug}/install",
    response_model=SuccessResponse[AppInstallation],
    status_code=status.HTTP_201_CREATED,
    summary="Install an app",
    operation_id="install_app",
)
async def install_app(
    store_id: UUID,
    slug: str,
    store_repo: StoreRepository = Depends(get_store_repository),
):
    """Idempotent: re-installing an already-installed app simply
    re-enables it (keeps existing settings)."""

    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )
    tenant_id = store.tenant_id

    async with AsyncSessionLocal() as session:
        app = (
            await session.execute(
                select(AppModel).where(
                    AppModel.slug == slug, AppModel.status == AppStatus.PUBLISHED
                )
            )
        ).scalar_one_or_none()
        if not app:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="App not found in catalog.",
            )

        # ON CONFLICT: re-enable the existing row, don't blow away
        # settings the merchant configured before disabling.
        stmt = (
            pg_insert(AppInstallationModel)
            .values(
                tenant_id=tenant_id,
                store_id=store_id,
                app_id=app.id,
                is_enabled=True,
                settings={},
            )
            .on_conflict_do_update(
                constraint="uq_app_installation_store_app",
                set_={"is_enabled": True},
            )
        )
        await session.execute(stmt)
        await session.commit()

        install = (
            await session.execute(
                select(AppInstallationModel).where(
                    AppInstallationModel.store_id == store_id,
                    AppInstallationModel.app_id == app.id,
                )
            )
        ).scalar_one()

    return SuccessResponse(
        data=AppInstallation(
            slug=app.slug,
            name=app.name,
            description=app.description,
            icon_url=app.icon_url,
            version=app.version,
            is_enabled=install.is_enabled,
            settings=install.settings or {},
            blocks=(app.manifest or {}).get("blocks", []) or [],
        ),
        message="App installed",
    )


@router.put(
    "/{slug}/settings",
    response_model=SuccessResponse[AppInstallation],
    summary="Update app settings",
    operation_id="update_app_settings",
)
async def update_settings(store_id: UUID, slug: str, body: UpdateSettingsRequest):
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(AppModel, AppInstallationModel)
                .join(AppInstallationModel, AppModel.id == AppInstallationModel.app_id)
                .where(AppInstallationModel.store_id == store_id, AppModel.slug == slug)
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Install not found"
            )
        app, install = row
        install.settings = body.settings or {}
        await session.commit()
        await session.refresh(install)

    return SuccessResponse(
        data=AppInstallation(
            slug=app.slug,
            name=app.name,
            description=app.description,
            icon_url=app.icon_url,
            version=app.version,
            is_enabled=install.is_enabled,
            settings=install.settings or {},
            blocks=(app.manifest or {}).get("blocks", []) or [],
        ),
        message="Settings updated",
    )


@router.post(
    "/{slug}/disable",
    response_model=SuccessResponse[AppInstallation],
    summary="Disable app",
    operation_id="disable_app",
)
async def disable_app(store_id: UUID, slug: str):
    return await _set_enabled(store_id, slug, enabled=False)


@router.post(
    "/{slug}/enable",
    response_model=SuccessResponse[AppInstallation],
    summary="Enable app",
    operation_id="enable_app",
)
async def enable_app(store_id: UUID, slug: str):
    return await _set_enabled(store_id, slug, enabled=True)


@router.delete(
    "/{slug}",
    response_model=SuccessResponse[dict[str, str]],
    summary="Uninstall app",
    operation_id="uninstall_app",
)
async def uninstall_app(store_id: UUID, slug: str):
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(AppInstallationModel)
                .join(AppModel, AppModel.id == AppInstallationModel.app_id)
                .where(AppInstallationModel.store_id == store_id, AppModel.slug == slug)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Install not found"
            )
        await session.delete(row)
        await session.commit()
    return SuccessResponse(data={"slug": slug}, message="App uninstalled")


async def _set_enabled(
    store_id: UUID, slug: str, *, enabled: bool
) -> SuccessResponse[AppInstallation]:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(AppModel, AppInstallationModel)
                .join(AppInstallationModel, AppModel.id == AppInstallationModel.app_id)
                .where(AppInstallationModel.store_id == store_id, AppModel.slug == slug)
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Install not found"
            )
        app, install = row
        install.is_enabled = enabled
        await session.commit()
        await session.refresh(install)

    return SuccessResponse(
        data=AppInstallation(
            slug=app.slug,
            name=app.name,
            description=app.description,
            icon_url=app.icon_url,
            version=app.version,
            is_enabled=install.is_enabled,
            settings=install.settings or {},
            blocks=(app.manifest or {}).get("blocks", []) or [],
        ),
        message="App enabled" if enabled else "App disabled",
    )
