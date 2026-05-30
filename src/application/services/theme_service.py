"""Theme service — business logic for the theme engine.

Orchestrates:
- Theme marketplace (list / detail)
- Store theme management (install / activate / uninstall / customize / publish)
- Storefront theme resolution (Next.js SSR → FastAPI internal call)

All methods are backward-compatible: publish/activate also write to
stores.theme_settings JSONB so existing components keep working during
the Next.js migration (Phase 2).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from src.core.entities.theme import StoreTheme, Theme, ThemeVersion
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository
from src.infrastructure.repositories.theme_repository import ThemeRepository
from src.infrastructure.repositories.theme_version_repository import (
    ThemeVersionRepository,
)

logger = logging.getLogger(__name__)


class ThemeService:
    """Coordinates all theme engine operations."""

    def __init__(
        self,
        theme_repo: ThemeRepository,
        version_repo: ThemeVersionRepository,
        store_theme_repo: StoreThemeRepository,
    ) -> None:
        self.theme_repo = theme_repo
        self.version_repo = version_repo
        self.store_theme_repo = store_theme_repo

    # ── Marketplace ────────────────────────────────────────────────────────────

    async def list_marketplace_themes(
        self,
        type_filter: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Return a paginated list of published themes."""
        skip = (page - 1) * per_page
        themes = await self.theme_repo.list_published(
            type_filter=type_filter, skip=skip, limit=per_page
        )
        total = await self.theme_repo.count_published(type_filter=type_filter)
        return {
            "themes": themes,
            "total": total,
            "page": page,
            "per_page": per_page,
        }

    async def get_theme_detail(self, slug: str) -> tuple[Theme, list[ThemeVersion]]:
        """Return a theme and all its versions. Raises 404 if not found."""
        theme = await self.theme_repo.get_by_slug(slug)
        if not theme:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Theme '{slug}' not found",
            )
        versions = await self.version_repo.list_for_theme(theme.id)
        return theme, versions

    # ── Store theme management ─────────────────────────────────────────────────

    async def install_theme(
        self,
        store_id: UUID,
        tenant_id: UUID,
        theme_id: UUID,
        version_id: UUID | None = None,
    ) -> StoreTheme:
        """Install a theme on a store.

        If the theme is already installed, returns the existing installation.
        Defaults to the latest published version if version_id is not specified.
        """
        # Check if already installed
        already = await self.store_theme_repo.installation_exists(store_id, theme_id)
        if already:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This theme is already installed on the store. "
                "Uninstall it first or activate the existing installation.",
            )

        # Resolve theme
        theme = await self.theme_repo.get_by_id(theme_id)
        if not theme:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Theme {theme_id} not found",
            )

        # Resolve version
        if version_id:
            version = await self.version_repo.get_by_id(version_id)
            if not version or version.theme_id != theme_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Theme version {version_id} not found",
                )
        else:
            version = await self.version_repo.get_latest_for_theme(theme_id)
            if not version:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Theme '{theme.slug}' has no published versions",
                )

        installation = StoreTheme(
            id=uuid4(),
            store_id=store_id,
            tenant_id=tenant_id,
            theme_id=theme_id,
            theme_version_id=version.id,
            is_active=False,
            installed_at=datetime.now(UTC),
        )
        result = await self.store_theme_repo.create(installation)

        logger.info(
            "theme_installed",
            extra={
                "store_id": str(store_id),
                "theme_slug": theme.slug,
                "version": version.version,
                "installation_id": str(result.id),
            },
        )
        return result

    async def activate_theme(
        self,
        store_id: UUID,
        installation_id: UUID,
        store_repo: Any,
    ) -> StoreTheme:
        """Activate an installed theme.

        Delegates the actual swap (snapshot → deactivate_all →
        upsert_active → mirror to marketplace_theme_installations) to
        ``ThemeActivationService`` so the three activate entrypoints —
        marketplace, dev-mode, V2 — go through one path. The pre-Phase-3a
        implementation open-coded the same snapshot/deactivate dance
        here; gap #2 closed it because the marketplace path was missing
        the dance entirely and the two implementations had drifted.

        Steps after the refactor:
        1. Look up the installation to grab its theme_id / version_id
           (the installation_id is a ``store_themes.id``; the
           activation service operates on the underlying theme + version
           pointers).
        2. Delegate to ``ThemeActivationService.activate``. Preserve
           existing customization by passing
           ``seed_customization_v3=None`` — the V2 path is "merchant
           toggles between already-installed themes", not "fresh
           install" — wiping their customization here would surprise
           them.
        3. Denormalize to ``stores.theme_settings`` (backward compat).
        4. Trigger Next.js on-demand revalidation.
        """
        from src.application.services.theme_activation_service import (
            ThemeActivationService,
        )
        from src.infrastructure.repositories.marketplace_repository import (
            MarketplaceRepository,
        )
        from src.infrastructure.repositories.store_theme_snapshot_repository import (
            StoreThemeSnapshotRepository,
        )

        installation = await self.store_theme_repo.get_installation(
            store_id, installation_id
        )
        if not installation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Installation not found",
            )

        snapshot_repo = StoreThemeSnapshotRepository(self.store_theme_repo.session)
        marketplace_repo = MarketplaceRepository(self.store_theme_repo.session)
        activation_svc = ThemeActivationService(
            store_theme_repo=self.store_theme_repo,
            snapshot_repo=snapshot_repo,
            marketplace_repo=marketplace_repo,
        )

        updated = await activation_svc.activate(
            store_id=store_id,
            tenant_id=installation.tenant_id,
            theme_id=installation.theme_id,
            theme_version_id=installation.theme_version_id,
            reason="pre-activation",
            # V2 path is non-marketplace by definition: clear any
            # stale marketplace install. The activation service
            # handles the None case by deactivating all marketplace
            # rows for the store.
            marketplace_theme_id=None,
            # None preserves existing customization on the upserted
            # row — the merchant flipped an already-installed theme;
            # they should land on whatever they had configured.
            seed_customization_v3=None,
        )

        # Backward-compat: denormalize to stores.theme_settings JSONB
        await self._denormalize_to_store(store_id, updated, store_repo)

        # Trigger Next.js on-demand revalidation
        await self._revalidate_storefront(store_id, store_repo, kind="theme_activate")

        logger.info(
            "theme_activated",
            extra={
                "store_id": str(store_id),
                "theme_slug": updated.theme_slug,
                "installation_id": str(installation_id),
            },
        )
        return updated

    async def uninstall_theme(
        self,
        store_id: UUID,
        installation_id: UUID,
    ) -> None:
        """Uninstall a theme from a store.

        Raises 400 if trying to uninstall the currently active theme.
        """
        installation = await self.store_theme_repo.get_installation(
            store_id, installation_id
        )
        if not installation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Installation not found",
            )
        if installation.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot uninstall the active theme. Activate another theme first.",
            )
        await self.store_theme_repo.delete(installation_id)

        logger.info(
            "theme_uninstalled",
            extra={
                "store_id": str(store_id),
                "installation_id": str(installation_id),
            },
        )

    async def save_draft_customization(
        self,
        store_id: UUID,
        installation_id: UUID,
        draft: dict[str, Any],
    ) -> StoreTheme:
        """Save (overwrite) the draft customization for an installation."""
        installation = await self.store_theme_repo.get_installation(
            store_id, installation_id
        )
        if not installation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Installation not found",
            )
        installation.save_draft(draft)
        return await self.store_theme_repo.update(installation)

    async def publish_customization(
        self,
        store_id: UUID,
        installation_id: UUID,
        store_repo: Any,
    ) -> StoreTheme:
        """Publish draft_customization → customization (goes live).

        Also denormalizes to stores.theme_settings for backward compat.
        Only allowed on the active installation.
        """
        installation = await self.store_theme_repo.get_installation(
            store_id, installation_id
        )
        if not installation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Installation not found",
            )
        if not installation.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only publish customization for the active theme",
            )
        installation.publish()
        updated = await self.store_theme_repo.update(installation)

        # Denormalize
        await self._denormalize_to_store(store_id, updated, store_repo)

        # Trigger Next.js on-demand revalidation
        await self._revalidate_storefront(
            store_id, store_repo, kind="customization_publish"
        )

        logger.info(
            "theme_customization_published",
            extra={"store_id": str(store_id), "installation_id": str(installation_id)},
        )
        return updated

    async def get_installations(self, store_id: UUID) -> list[StoreTheme]:
        """Return all theme installations for a store."""
        return await self.store_theme_repo.get_installations_for_store(store_id)

    async def get_installation(
        self, store_id: UUID, installation_id: UUID
    ) -> StoreTheme:
        """Return a single installation; raises 404 if not found."""
        installation = await self.store_theme_repo.get_installation(
            store_id, installation_id
        )
        if not installation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Installation not found",
            )
        return installation

    # ── Storefront resolution ──────────────────────────────────────────────────

    async def resolve_storefront_theme(
        self,
        store_id: UUID,
        draft_installation_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Resolve the active theme for a store (called by storefront SSR).

        Returns a dict with all theme data needed for server-side rendering.
        Always populates BOTH the legacy `customization` flat shape (for
        the older Vite SPA storefront) and a `customization_v3` shape (for
        the Next.js storefront / @numu/theme-sdk). The two shapes describe
        the same published state — `customization_v3` is just the V3-
        normalized view of `customization` when no V3 data exists yet.

        - Normal mode: returns the LIVE active installation + customization.
        - Draft mode (`draft_installation_id` set): returns the same
          installation but with draft data in place of live (preview mode).
        """
        from src.application.services.theme_v3_mappers import (
            resolve_theme_settings,
        )

        if draft_installation_id is not None:
            inst = await self.store_theme_repo.get_installation(
                store_id, draft_installation_id
            )
            if not inst:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Installation {draft_installation_id} not found",
                )
            # Pick draft if the installation has unsaved changes; otherwise
            # the live customization. Same rule for both shapes.
            use_draft = inst.has_draft_changes
            legacy_customization = (
                inst.draft_customization if use_draft else inst.customization
            )
            v3_customization = (
                inst.draft_customization_v3 if use_draft else inst.customization_v3
            ) or {}
            v3_resolved = resolve_theme_settings(
                customization_v3=v3_customization,
                legacy_settings=legacy_customization,
            ).model_dump()
            return {
                "theme_id": str(inst.theme_id),
                "theme_slug": inst.theme_slug,
                "theme_type": inst.theme_type.value if inst.theme_type else "internal",
                "version": inst.theme_version,
                "bundle_url": inst.bundle_url,
                "css_url": inst.css_url,
                "customization": legacy_customization,
                "customization_v3": v3_resolved,
                "settings_schema": inst.settings_schema or {},
                "section_schemas": inst.section_schemas,
                "installation_id": str(inst.id),
                "bundle_checksum": getattr(inst, "bundle_checksum", None),
            }

        active = await self.store_theme_repo.get_active_for_store(store_id)
        if not active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No active theme found for store {store_id}",
            )
        v3_resolved = resolve_theme_settings(
            customization_v3=active.customization_v3 or {},
            legacy_settings=active.customization,
        ).model_dump()
        return {
            "theme_id": str(active.theme_id),
            "theme_slug": active.theme_slug,
            "theme_type": active.theme_type.value if active.theme_type else "internal",
            "version": active.theme_version,
            "bundle_url": active.bundle_url,
            "css_url": active.css_url,
            "customization": active.customization,
            "customization_v3": v3_resolved,
            "settings_schema": active.settings_schema or {},
            "section_schemas": active.section_schemas,
            "installation_id": str(active.id),
            "bundle_checksum": getattr(active, "bundle_checksum", None),
        }

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _denormalize_to_store(
        self,
        store_id: UUID,
        installation: StoreTheme,
        store_repo: Any,
    ) -> None:
        """Write active theme info back to stores.theme_settings JSONB.

        This keeps the existing storefront / dashboard code working during
        the migration period (Phase 1 → Phase 2). The Next.js storefront will
        switch to using the new /storefront/theme/{store_id} endpoint.
        """
        try:
            store = await store_repo.get_by_id(store_id)
            if not store:
                return
            # Start from any existing theme_settings so we don't drop fields
            # that aren't part of the new customization schema.
            theme_settings: dict[str, Any] = dict(store.theme_settings or {})

            # Merge top-level customization keys first (e.g. templates, navigation)
            # but HANDLE "theme" sub-dict separately so we don't clobber base_theme.
            if installation.customization:
                for key, value in installation.customization.items():
                    if key == "theme" and isinstance(value, dict):
                        # Deep-merge theme section
                        existing_theme = (
                            dict(theme_settings.get("theme") or {})
                            if isinstance(theme_settings.get("theme"), dict)
                            else {}
                        )
                        existing_theme.update(value)
                        theme_settings["theme"] = existing_theme
                    else:
                        theme_settings[key] = value

            # Now force our identity fields into theme.* AFTER the merge so they
            # always win, regardless of what was in the customization.
            if not isinstance(theme_settings.get("theme"), dict):
                theme_settings["theme"] = {}
            theme_settings["theme"]["base_theme"] = installation.theme_slug
            theme_settings["theme"]["installation_id"] = str(installation.id)

            store.theme_settings = theme_settings
            await store_repo.update(store)
        except Exception as exc:
            # Non-fatal — log and continue; new tables are the source of truth
            logger.warning(
                "theme_denormalize_failed",
                extra={"store_id": str(store_id), "error": str(exc)},
            )

    async def _revalidate_storefront(
        self,
        store_id: UUID,
        store_repo: Any,
        kind: str,
    ) -> None:
        """Trigger Next.js on-demand revalidation for this store.

        Non-fatal — if the Next.js app is down or the secret is missing,
        we log and continue. The ISR cache will refresh naturally within
        its revalidate window (60s).
        """
        try:
            from src.infrastructure.external_services.nextjs_revalidation import (
                revalidate_on_customization_publish,
                revalidate_on_theme_activate,
            )

            store = await store_repo.get_by_id(store_id)
            if not store or not store.subdomain:
                return

            if kind == "theme_activate":
                await revalidate_on_theme_activate(store.subdomain, str(store_id))
            elif kind == "customization_publish":
                await revalidate_on_customization_publish(
                    store.subdomain, str(store_id)
                )
        except Exception as exc:
            logger.warning(
                "storefront_revalidate_failed",
                extra={"store_id": str(store_id), "kind": kind, "error": str(exc)},
            )
