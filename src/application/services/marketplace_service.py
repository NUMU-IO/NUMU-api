"""Marketplace service layer for theme marketplace operations.

Owns the developer/admin/install workflows. The repository layer holds
the SQL; this layer orchestrates side-effects (build queue, install
counters, store_theme activation) and enforces ownership/state
preconditions.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.application.services.theme_v3_presets import (
    generate_initial_v3_customization,
)
from src.core.entities.marketplace_theme import (
    MarketplaceThemeStatus,
    MarketplaceVersionStatus,
)

logger = logging.getLogger(__name__)


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w\.\-]+)?$")
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9\-]{0,62}[a-z0-9])?$")


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise ValueError(
            "slug must be lowercase letters, digits, or hyphens (3-64 chars)"
        )


def _validate_semver(version: str) -> None:
    if not _SEMVER_RE.match(version):
        raise ValueError(
            "version_string must be semver (e.g. '1.0.0' or '1.0.0-beta.1')"
        )


class MarketplaceService:
    """Business logic for marketplace theme operations."""

    def __init__(
        self,
        marketplace_repo,
        store_theme_repo=None,
        store_repo=None,
    ):
        self._marketplace_repo = marketplace_repo
        self._store_theme_repo = store_theme_repo
        self._store_repo = store_repo

    # ── Developer flows ──────────────────────────────────────────────────────

    async def create_listing(
        self, developer_id: UUID, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a new marketplace theme listing (status=draft).

        The slug must be unique across the marketplace.
        """
        slug = data.get("slug", "").strip().lower()
        _validate_slug(slug)
        if await self._marketplace_repo.get_theme_by_slug(slug):
            raise ValueError(f"slug already taken: {slug!r}")

        theme = await self._marketplace_repo.create_theme({
            "developer_id": developer_id,
            "name": data["name"],
            "slug": slug,
            "description": data.get("description"),
            "short_description": data.get("short_description"),
            "price_cents": data.get("price_cents", 0),
            "currency": data.get("currency", "USD"),
            "status": MarketplaceThemeStatus.DRAFT.value,
            "thumbnail_url": data.get("thumbnail_url"),
            "preview_url": data.get("preview_url"),
            "demo_store_url": data.get("demo_store_url"),
            "tags": data.get("tags", []),
            "category": data.get("category"),
            "supported_languages": data.get("supported_languages", ["en", "ar"]),
            "supported_features": data.get("supported_features", {}),
        })
        return self._theme_dict(theme)

    async def update_listing(
        self,
        developer_id: UUID,
        theme_id: UUID,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a marketplace theme listing (developer-owned)."""
        theme = await self._marketplace_repo.get_theme_by_id(theme_id)
        if not theme or theme.developer_id != developer_id:
            raise ValueError("theme not found or not owned by developer")

        # Restrict which fields are mutable
        allowed = {
            "name",
            "description",
            "short_description",
            "thumbnail_url",
            "preview_url",
            "demo_store_url",
            "tags",
            "category",
            "supported_languages",
            "supported_features",
            "price_cents",
            "currency",
        }
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return self._theme_dict(theme)
        clean["updated_at"] = datetime.now(UTC)
        updated = await self._marketplace_repo.update_theme(theme_id, clean)
        return self._theme_dict(updated)

    async def list_my_themes(self, developer_id: UUID) -> list[dict[str, Any]]:
        themes = await self._marketplace_repo.list_by_developer(developer_id)
        return [self._theme_dict(t) for t in themes]

    async def submit_version(
        self,
        developer_id: UUID,
        theme_id: UUID,
        version_string: str,
        source_zip_path: str,
        release_notes: str | None = None,
    ) -> dict[str, Any]:
        """Submit a new version for build.

        `source_zip_path` is the on-disk path of the ZIP uploaded via
        /themes/upload — accepted because the upload path is already
        authenticated. The Celery worker reads this path and runs the
        sandboxed build pipeline; on success it stamps bundle_url, css_url,
        checksum, etc. and moves the version into pending_review.
        """
        _validate_semver(version_string)

        theme = await self._marketplace_repo.get_theme_by_id(theme_id)
        if not theme or theme.developer_id != developer_id:
            raise ValueError("theme not found or not owned by developer")

        # Reject duplicate version strings for the same listing
        for v in await self._marketplace_repo.list_versions(theme_id):
            if v.version_string == version_string:
                raise ValueError(
                    f"version {version_string!r} already exists for this theme"
                )

        version = await self._marketplace_repo.create_version({
            "theme_id": theme_id,
            "version_string": version_string,
            "status": MarketplaceVersionStatus.PENDING_BUILD.value,
            "release_notes": release_notes,
            "source_zip_path": source_zip_path,
        })

        # Move the listing into pending_review while it has work in flight
        if theme.status in (
            MarketplaceThemeStatus.DRAFT,
            MarketplaceThemeStatus.REJECTED,
        ):
            await self._marketplace_repo.update_theme(
                theme_id,
                {"status": MarketplaceThemeStatus.PENDING_REVIEW.value},
            )

        # Dispatch build task — failures here surface to the caller because
        # leaving a row in pending_build with no worker would silently stall.
        from src.infrastructure.messaging.tasks.theme_marketplace_tasks import (
            build_marketplace_theme,
        )

        try:
            build_marketplace_theme.delay(str(version.id))
        except Exception as exc:  # pragma: no cover — broker outages
            logger.error(
                "marketplace_build_dispatch_failed",
                extra={"version_id": str(version.id), "error": str(exc)},
            )
            await self._marketplace_repo.update_version(
                version.id,
                {
                    "status": MarketplaceVersionStatus.BUILD_FAILED.value,
                    "build_log": f"Failed to enqueue build: {exc}",
                },
            )
            raise

        return {
            "version_id": str(version.id),
            "status": version.status.value,
        }

    async def get_version_status(
        self, developer_id: UUID, version_id: UUID
    ) -> dict[str, Any]:
        version = await self._marketplace_repo.get_version_by_id(version_id)
        if not version:
            raise ValueError(f"version {version_id} not found")
        theme = await self._marketplace_repo.get_theme_by_id(version.theme_id)
        if not theme or theme.developer_id != developer_id:
            raise ValueError("not authorized for this version")
        return {
            "version_id": str(version.id),
            "version_string": version.version_string,
            "status": version.status.value,
            "build_log": version.build_log,
            "bundle_url": version.bundle_url,
            "css_url": version.css_url,
            "size_bytes": version.size_bytes,
            "checksum": version.checksum,
        }

    async def list_versions(
        self, developer_id: UUID, theme_id: UUID
    ) -> list[dict[str, Any]]:
        theme = await self._marketplace_repo.get_theme_by_id(theme_id)
        if not theme or theme.developer_id != developer_id:
            raise ValueError("theme not found or not owned by developer")
        versions = await self._marketplace_repo.list_versions(theme_id)
        return [
            {
                "id": str(v.id),
                "version_string": v.version_string,
                "status": v.status.value,
                "release_notes": v.release_notes,
                "bundle_url": v.bundle_url,
                "css_url": v.css_url,
                "checksum": v.checksum,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ]

    # ── Admin flows ──────────────────────────────────────────────────────────

    async def list_pending_reviews(self) -> list[dict[str, Any]]:
        versions = await self._marketplace_repo.list_pending_review()
        out = []
        for v in versions:
            theme = await self._marketplace_repo.get_theme_by_id(v.theme_id)
            out.append({
                "version_id": str(v.id),
                "version_string": v.version_string,
                "theme_id": str(v.theme_id),
                "theme_name": theme.name if theme else None,
                "theme_slug": theme.slug if theme else None,
                "developer_id": str(theme.developer_id) if theme else None,
                "release_notes": v.release_notes,
                "size_bytes": v.size_bytes,
                "checksum": v.checksum,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            })
        return out

    async def review_version(
        self,
        reviewer_id: UUID,
        version_id: UUID,
        decision: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Admin reviews a version (approve/reject)."""
        if decision not in ("approve", "reject"):
            raise ValueError("decision must be 'approve' or 'reject'")

        version = await self._marketplace_repo.get_version_by_id(version_id)
        if not version:
            raise ValueError(f"version {version_id} not found")
        if version.status != MarketplaceVersionStatus.PENDING_REVIEW:
            raise ValueError(
                f"version is not pending_review (status={version.status.value})"
            )

        new_version_status = (
            MarketplaceVersionStatus.PUBLISHED
            if decision == "approve"
            else MarketplaceVersionStatus.REJECTED
        )
        await self._marketplace_repo.update_version(
            version_id,
            {
                "status": new_version_status.value,
                "review_notes": notes,
                "reviewed_by": reviewer_id,
            },
        )

        # Promote the theme listing on first approval
        theme = await self._marketplace_repo.get_theme_by_id(version.theme_id)
        if decision == "approve" and theme:
            await self._marketplace_repo.update_theme(
                theme.id,
                {"status": MarketplaceThemeStatus.PUBLISHED.value},
            )
        elif decision == "reject" and theme:
            await self._marketplace_repo.update_theme(
                theme.id,
                {"status": MarketplaceThemeStatus.REJECTED.value},
            )

        return {
            "version_id": str(version_id),
            "status": new_version_status.value,
        }

    # ── Public catalog ───────────────────────────────────────────────────────

    async def browse_themes(
        self,
        page: int = 1,
        per_page: int = 20,
        category: str | None = None,
    ) -> dict[str, Any]:
        themes, total = await self._marketplace_repo.list_published(
            page, per_page, category
        )
        return {
            "themes": [self._theme_dict(t, public=True) for t in themes],
            "total": total,
            "page": page,
            "per_page": per_page,
        }

    async def get_theme_detail(self, slug: str) -> dict[str, Any]:
        theme = await self._marketplace_repo.get_theme_by_slug(slug)
        if not theme or theme.status != MarketplaceThemeStatus.PUBLISHED:
            raise ValueError(f"theme not found: {slug!r}")
        latest = await self._marketplace_repo.get_latest_published_version(theme.id)
        return {
            **self._theme_dict(theme, public=True),
            "latest_version": (
                {
                    "id": str(latest.id),
                    "version_string": latest.version_string,
                    "release_notes": latest.release_notes,
                    "bundle_url": latest.bundle_url,
                    "css_url": latest.css_url,
                    "settings_schema": latest.settings_schema,
                    "section_schemas": latest.section_schemas,
                    "size_bytes": latest.size_bytes,
                }
                if latest
                else None
            ),
        }

    # ── Store install / activate / uninstall ─────────────────────────────────

    async def list_installed(self, store_id: UUID) -> list[dict[str, Any]]:
        installs = await self._marketplace_repo.list_installations(store_id)
        out = []
        for i in installs:
            theme = await self._marketplace_repo.get_theme_by_id(i.marketplace_theme_id)
            version = await self._marketplace_repo.get_version_by_id(
                i.marketplace_version_id
            )
            out.append({
                "installation_id": str(i.id),
                "is_active": i.is_active,
                "installed_at": i.installed_at.isoformat() if i.installed_at else None,
                "theme": self._theme_dict(theme, public=True) if theme else None,
                "version": (
                    {
                        "id": str(version.id),
                        "version_string": version.version_string,
                        "bundle_url": version.bundle_url,
                        "css_url": version.css_url,
                    }
                    if version
                    else None
                ),
            })
        return out

    async def install_theme(
        self,
        store_id: UUID,
        marketplace_theme_id: UUID,
    ) -> dict[str, Any]:
        """Install (or reinstall) the latest published version of a theme."""
        theme = await self._marketplace_repo.get_theme_by_id(marketplace_theme_id)
        if not theme or theme.status != MarketplaceThemeStatus.PUBLISHED:
            raise ValueError("theme not found or not published")

        version = await self._marketplace_repo.get_latest_published_version(
            marketplace_theme_id
        )
        if not version or not version.bundle_url:
            raise ValueError("no published version available for this theme")

        installation = await self._marketplace_repo.create_or_reactivate_installation(
            store_id=store_id,
            marketplace_theme_id=marketplace_theme_id,
            marketplace_version_id=version.id,
        )
        await self._marketplace_repo.increment_install_count(marketplace_theme_id)

        return {
            "installation_id": str(installation.id),
            "marketplace_theme_id": str(marketplace_theme_id),
            "marketplace_version_id": str(version.id),
            "is_active": installation.is_active,
        }

    async def activate_theme(
        self,
        store_id: UUID,
        marketplace_theme_id: UUID,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Activate an installed marketplace theme.

        Side effects:
        - Marks the install row active (deactivating any other marketplace
          install for the store).
        - Initializes the store's V3 draft from the version's presets so the
          editor has something sensible to render on first open.
        - Triggers Next.js cache invalidation (non-fatal).
        """
        installation = await self._marketplace_repo.get_installation(
            store_id, marketplace_theme_id
        )
        if not installation or installation.uninstalled_at is not None:
            raise ValueError("theme is not installed for this store")

        version = await self._marketplace_repo.get_version_by_id(
            installation.marketplace_version_id
        )
        if not version or not version.bundle_url:
            raise ValueError(
                "installed version is missing a bundle — reinstall the theme"
            )

        await self._marketplace_repo.set_active_installation(
            store_id, marketplace_theme_id
        )

        # Seed the V3 draft from the version's presets so the editor has
        # the developer's canonical starting layout. This is the BYOT
        # equivalent of activate_theme on a built-in theme.
        if self._store_theme_repo is not None:
            store_theme = await self._store_theme_repo.get_active_for_store(store_id)
            if store_theme is not None:
                v3 = generate_initial_v3_customization(
                    theme_id=str(marketplace_theme_id),
                    presets=version.presets,
                    bundle_url=version.bundle_url,
                    css_url=version.css_url,
                    settings_schema=version.settings_schema,
                    section_schemas=version.section_schemas,
                )
                store_theme.draft_customization_v3 = v3.model_dump()
                await self._store_theme_repo.update(store_theme)

        # Revalidate storefront (non-fatal)
        await self._revalidate(store_id)

        logger.info(
            "marketplace_theme_activated",
            extra={
                "store_id": str(store_id),
                "marketplace_theme_id": str(marketplace_theme_id),
                "version_id": str(version.id),
                "user_id": str(user_id) if user_id else None,
            },
        )

        return {
            "marketplace_theme_id": str(marketplace_theme_id),
            "marketplace_version_id": str(version.id),
            "is_active": True,
        }

    async def uninstall_theme(self, store_id: UUID, marketplace_theme_id: UUID) -> bool:
        ok = await self._marketplace_repo.mark_uninstalled(
            store_id, marketplace_theme_id
        )
        if ok:
            await self._marketplace_repo.increment_install_count(
                marketplace_theme_id, delta=-1
            )
        return ok

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _theme_dict(self, t, public: bool = False) -> dict[str, Any]:
        if t is None:
            return {}
        base = {
            "id": str(t.id),
            "name": t.name,
            "slug": t.slug,
            "description": t.description,
            "short_description": t.short_description,
            "price_cents": t.price_cents,
            "currency": t.currency,
            "status": t.status.value if hasattr(t.status, "value") else t.status,
            "thumbnail_url": t.thumbnail_url,
            "preview_url": t.preview_url,
            "demo_store_url": t.demo_store_url,
            "tags": t.tags,
            "category": t.category,
            "supported_languages": t.supported_languages,
            "supported_features": t.supported_features,
            "install_count": t.install_count,
            "average_rating": t.average_rating,
            "review_count": t.review_count,
        }
        if not public:
            base["developer_id"] = str(t.developer_id)
        return base

    async def _revalidate(self, store_id: UUID) -> None:
        if not self._store_repo:
            return
        try:
            from src.infrastructure.external_services.nextjs_revalidation import (
                revalidate_on_theme_activate,
            )

            store = await self._store_repo.get_by_id(store_id)
            if not store or not store.subdomain:
                return
            await revalidate_on_theme_activate(store.subdomain, str(store_id))
        except Exception as exc:
            logger.warning(
                "marketplace_revalidate_failed",
                extra={"store_id": str(store_id), "error": str(exc)},
            )
