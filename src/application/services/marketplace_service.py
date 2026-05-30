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
        theme_repo=None,
        version_repo=None,
    ):
        self._marketplace_repo = marketplace_repo
        self._store_theme_repo = store_theme_repo
        self._store_repo = store_repo
        # Phase 3a: marketplace activate now bridges to the runtime
        # themes / theme_versions tables (so store_themes.theme_id has
        # somewhere to point). Optional for read-only routes that don't
        # touch the activate path.
        self._theme_repo = theme_repo
        self._version_repo = version_repo

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
        """Build a rich review packet per pending version.

        Each item carries enough data for the admin to decide
        approve/reject without further round-trips: listing metadata,
        marketing assets, developer profile (with a count of their
        prior themes — proxy for trust), version artifacts (bundle
        URL, build log, security scan signals), schemas, and the
        theme's full version history.

        We accept the N+1 query cost because pending queues are tiny
        (typically <50 items at any time). If this ever needs to scale
        we can batch-fetch developers + sibling versions; not worth
        the complexity today.
        """
        versions = await self._marketplace_repo.list_pending_review()
        out: list[dict[str, Any]] = []
        for v in versions:
            theme = await self._marketplace_repo.get_theme_by_id(v.theme_id)

            # Developer profile via a lazy import — we don't want a
            # hard dep from MarketplaceService onto UserRepository in
            # the constructor (some callsites construct the service
            # without a full DI graph). Fall back to None on any
            # lookup error so a stale developer FK doesn't break
            # the whole pending list.
            dev_email: str | None = None
            dev_name: str | None = None
            dev_total = 0
            dev_published = 0
            if theme is not None and theme.developer_id is not None:
                try:
                    from src.api.dependencies.repositories import (
                        get_user_repository,
                    )

                    # Construct via the same session the marketplace
                    # repo is bound to — avoids opening a second DB
                    # connection per pending item.
                    user_repo = get_user_repository(
                        session=self._marketplace_repo._session,  # type: ignore[attr-defined]
                    )
                    user = await user_repo.get_by_id(theme.developer_id)
                    if user is not None:
                        dev_email = str(user.email)
                        dev_name = (
                            f"{user.first_name or ''} {user.last_name or ''}"
                        ).strip() or None
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "pending_review_developer_lookup_failed",
                        extra={
                            "version_id": str(v.id),
                            "developer_id": str(theme.developer_id),
                            "error": str(exc),
                        },
                    )

                try:
                    dev_themes = await self._marketplace_repo.list_by_developer(
                        theme.developer_id
                    )
                    dev_total = len(dev_themes)
                    dev_published = sum(
                        1
                        for t in dev_themes
                        if t.status == MarketplaceThemeStatus.PUBLISHED
                    )
                except Exception:
                    pass

            # Version history for this theme — most recent first, all
            # statuses included so the admin sees prior failures /
            # rejections.
            history: list[dict[str, Any]] = []
            try:
                sibling_versions = await self._marketplace_repo.list_versions(
                    v.theme_id
                )
                for sv in sibling_versions:
                    history.append({
                        "version_id": str(sv.id),
                        "version_string": sv.version_string,
                        "status": sv.status.value,
                        "size_bytes": sv.size_bytes,
                        "checksum": sv.checksum,
                        "created_at": sv.created_at.isoformat()
                        if sv.created_at
                        else None,
                        "is_current": sv.id == v.id,
                    })
            except Exception:
                pass

            out.append({
                # Identity
                "version_id": str(v.id),
                "version_string": v.version_string,
                "theme_id": str(v.theme_id),
                "release_notes": v.release_notes,
                "size_bytes": v.size_bytes,
                "checksum": v.checksum,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "bundle_url": v.bundle_url,
                "css_url": v.css_url,
                "build_log": v.build_log,
                # Listing
                "theme_name": theme.name if theme else None,
                "theme_slug": theme.slug if theme else None,
                "theme_description": theme.description if theme else None,
                "theme_short_description": (theme.short_description if theme else None),
                "theme_category": theme.category if theme else None,
                "theme_tags": list(theme.tags) if theme else [],
                "theme_supported_languages": (
                    list(theme.supported_languages) if theme else []
                ),
                "theme_supported_features": (
                    dict(theme.supported_features) if theme else {}
                ),
                "theme_status": theme.status.value if theme else None,
                "price_cents": theme.price_cents if theme else 0,
                "currency": theme.currency if theme else "USD",
                # Marketing
                "thumbnail_url": theme.thumbnail_url if theme else None,
                "preview_url": theme.preview_url if theme else None,
                "demo_store_url": theme.demo_store_url if theme else None,
                # Developer
                "developer_id": (str(theme.developer_id) if theme else None),
                "developer_email": dev_email,
                "developer_name": dev_name,
                "developer_total_themes": dev_total,
                "developer_published_themes": dev_published,
                # Schemas
                "settings_schema": v.settings_schema,
                "section_schemas": v.section_schemas,
                # History
                "version_history": history,
            })
        return out

    async def list_all_themes_admin(self) -> list[dict[str, Any]]:
        """List every marketplace theme (any status) for admin flag UI.
        Bypasses the catalog_visible filter so admins can see + flip
        invisible themes."""
        themes = await self._marketplace_repo.list_all()
        return [_admin_theme_dict(t) for t in themes]

    async def update_theme_flags(
        self,
        theme_id: UUID,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge ``patch`` into the theme's flags JSONB. Returns the
        admin-facing dict for the updated theme."""
        updated = await self._marketplace_repo.merge_flags(theme_id, patch)
        if updated is None:
            raise ValueError(f"theme not found: {theme_id}")
        return _admin_theme_dict(updated)

    async def update_theme_metadata(
        self,
        theme_id: UUID,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Patch admin-curated metadata on a marketplace theme (file 04 §6.1).

        ``patch`` is expected to be the output of
        ``AdminThemeMetadataPatch.model_dump(exclude_unset=True)`` so the
        caller has already stripped unset fields and validated bounds
        (``price_cents >= 0``, ``currency`` whitelist, image-URL allowlist).
        This method just persists.

        Returns the full admin-facing dict including the freshly-written
        metadata fields. Used by the merchant-hub admin UI to refresh its
        row in-place without a follow-up GET.

        Raises:
            ValueError: theme does not exist (the route maps to HTTP 404).
        """
        if not patch:
            existing = await self._marketplace_repo.get_theme_by_id(theme_id)
            if existing is None:
                raise ValueError(f"theme not found: {theme_id}")
            return _admin_metadata_dict(existing)

        updated = await self._marketplace_repo.update_theme(theme_id, patch)
        if updated is None:
            raise ValueError(f"theme not found: {theme_id}")
        return _admin_metadata_dict(updated)

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
        user_id: str | None = None,
    ) -> dict[str, Any]:
        themes, total = await self._marketplace_repo.list_published(
            page, per_page, category, user_id=user_id
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
            # File 04 §8 — MarketplaceThemeDetail extras over the card
            "author_url": getattr(theme, "author_url", None),
            "highlights": _highlights_to_dicts(getattr(theme, "highlights", []) or []),
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

    async def developer_install_theme(
        self,
        *,
        developer_id: UUID,
        store_id: UUID,
        marketplace_theme_id: UUID,
    ) -> dict[str, Any]:
        """Install a developer's own theme on one of their own stores.

        Bypasses two gates the public install path enforces:
          * the listing must be `published` (we accept any status as long
            as the developer owns the listing).
          * the latest version must be `published` (we use the latest
            version that has a bundle_url — typically the most recent
            successful build, even if pending_review).

        Still enforced:
          * developer must own the listing (theme.developer_id check).
          * a usable build must exist (bundle_url IS NOT NULL).
          * verify_store_ownership at the route layer ensures the dev
            owns the target store.

        Free vs paid is irrelevant here — a developer doesn't pay
        themselves for their own theme, so we never look at price_cents.
        """
        theme = await self._marketplace_repo.get_theme_by_id(marketplace_theme_id)
        if theme is None:
            raise ValueError("theme not found")
        if theme.developer_id != developer_id:
            # Don't leak listing existence — a stranger gets the same
            # 404 a deleted listing would surface.
            raise ValueError("theme not found")

        version = await self._marketplace_repo.get_latest_installable_version(
            marketplace_theme_id
        )
        if version is None or not version.bundle_url:
            raise ValueError(
                "No installable build yet. Wait for the build to finish or "
                "submit a new version."
            )

        installation = await self._marketplace_repo.create_or_reactivate_installation(
            store_id=store_id,
            marketplace_theme_id=marketplace_theme_id,
            marketplace_version_id=version.id,
        )
        # We deliberately DON'T bump the public install_count for
        # developer self-installs; they'd inflate marketplace metrics.
        return {
            "installation_id": str(installation.id),
            "marketplace_theme_id": str(marketplace_theme_id),
            "marketplace_version_id": str(version.id),
            "version_string": version.version_string,
            "version_status": version.status.value,
            "is_active": installation.is_active,
            "is_developer_install": True,
        }

    async def install_theme(
        self,
        store_id: UUID,
        marketplace_theme_id: UUID,
        *,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Install (or reinstall) the latest published version of a theme.

        Paid themes (`price_cents > 0`) require a succeeded, non-refunded
        purchase by `user_id` for the same `marketplace_theme_id`. Free
        themes skip the check. Pass `user_id=None` for system-initiated
        installs (e.g. internal tooling); production callers should
        always supply a real user.
        """
        theme = await self._marketplace_repo.get_theme_by_id(marketplace_theme_id)
        if not theme or theme.status != MarketplaceThemeStatus.PUBLISHED:
            raise ValueError("theme not found or not published")

        if theme.price_cents > 0:
            if user_id is None:
                raise ValueError(
                    "Paid themes require an authenticated user_id at install time"
                )
            owns = await self._marketplace_repo.has_active_purchase(
                user_id, marketplace_theme_id
            )
            if not owns:
                raise ValueError(
                    "This theme requires a purchase. Buy it via /checkout-session "
                    "before installing."
                )

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

        Phase 3a closes gap #2 — this used to flip
        ``marketplace_theme_installations.is_active`` only, leaving
        ``store_themes`` pointing at the previous theme. The customizer
        reads ``store_themes`` so merchants would see the OLD theme
        after clicking Activate. The fix is to delegate the swap to the
        shared ``ThemeActivationService``, which mutates both tables
        in the right order with a snapshot in between.

        The marketplace catalog table (``marketplace_themes``) is
        separate from the runtime ``themes`` table (the customizer's
        source of truth), so this method also has to bridge the two:
        get-or-create a ``themes`` row by slug and a ``theme_versions``
        row matching the marketplace version's bundle. Without that
        bridge, ``store_themes.theme_id`` has nowhere valid to point.

        Side effects after this method:
        - ``store_themes.is_active=True`` on a row whose ``theme_id``
          matches the runtime theme bridged from this marketplace theme.
        - ``marketplace_theme_installations.is_active=True`` on the
          named install (and ``False`` on every other marketplace install
          for the store).
        - A ``store_theme_snapshots`` row with
          ``reason="marketplace-activate"`` when a prior different theme
          was active.
        - ``stores.settings.customization`` stamped with ``is_published``
          + ``last_published_at`` so the merchant-hub Themes page renders
          a fresh "Published · today" badge.
        - Next.js storefront cache invalidation (non-fatal).
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

        # The activate path needs the runtime theme / version repos to
        # bridge marketplace_themes → themes. Callers that build this
        # service without them (e.g. catalog-only routes) can't activate.
        if (
            self._store_theme_repo is None
            or self._theme_repo is None
            or self._version_repo is None
        ):
            raise ValueError(
                "MarketplaceService.activate_theme requires store_theme_repo, "
                "theme_repo, and version_repo to be wired (see "
                "marketplace/store_install.py _svc dependency)."
            )

        # Get the catalog row so we have the slug + display name to
        # mirror onto the runtime theme entity.
        marketplace_theme = await self._marketplace_repo.get_theme_by_id(
            marketplace_theme_id
        )
        if marketplace_theme is None:
            raise ValueError("marketplace theme not found")

        # 1) Bridge to the runtime themes table. The slug is the natural
        #    bridging key — dev-mode also uses it (manifest.id ==
        #    marketplace_theme.slug for any theme that was ever connected
        #    locally). When this is the merchant's first time activating
        #    via the marketplace, we create the row fresh.
        #
        # The runtime Theme entity has stricter types than the
        # marketplace catalog version (author is a required str with
        # default "NUMU"; section_schemas is dict|None). The
        # marketplace version permits list-or-dict for section_schemas
        # because Shopify ships them as arrays. Coerce list → {} so
        # the runtime row stays dict-shaped; the editor reads the
        # bundle's import-map for the canonical schemas anyway.
        def _coerce_section_schemas(s):
            if isinstance(s, dict):
                return s
            if isinstance(s, list):
                # Shopify-style array of {type, name, settings, ...}
                return {
                    e.get("type", f"__noid_{i}"): e
                    for i, e in enumerate(s)
                    if isinstance(e, dict)
                }
            return {}

        runtime_theme = await self._theme_repo.get_by_slug(marketplace_theme.slug)
        if runtime_theme is None:
            from uuid import uuid4 as _uuid4

            from src.core.entities.theme import Theme, ThemeStatus, ThemeType

            runtime_theme = Theme(
                id=_uuid4(),
                name=marketplace_theme.name,
                slug=marketplace_theme.slug,
                description=marketplace_theme.description,
                author="Marketplace",
                type=ThemeType.EXTERNAL,
                status=ThemeStatus.PUBLISHED,
                is_public=False,
                settings_schema=(version.settings_schema or {}),
                section_schemas=_coerce_section_schemas(version.section_schemas),
                supported_features=marketplace_theme.supported_features or None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            runtime_theme = await self._theme_repo.create(runtime_theme)
        else:
            # Refresh schemas to the activated version's so the editor
            # reflects the bundle the storefront will load. Marketplace
            # themes can roll versions independently of the runtime row.
            runtime_theme.settings_schema = version.settings_schema or {}
            runtime_theme.section_schemas = _coerce_section_schemas(
                version.section_schemas
            )
            runtime_theme.name = marketplace_theme.name
            runtime_theme.description = marketplace_theme.description
            runtime_theme = await self._theme_repo.update(runtime_theme)

        # 2) Mint a runtime theme_versions row for the marketplace bundle.
        #    Fresh row each activate so a re-activate after a publish
        #    serves the latest bundle without stale caching.
        from uuid import uuid4 as _uuid4_v

        from src.core.entities.theme import ThemeVersion as _RuntimeThemeVersion

        runtime_version = _RuntimeThemeVersion(
            id=_uuid4_v(),
            theme_id=runtime_theme.id,
            version=f"{version.version_string}+mp.{int(datetime.now(UTC).timestamp())}",
            bundle_url=version.bundle_url,
            css_url=version.css_url,
            manifest={
                "id": marketplace_theme.slug,
                "name": marketplace_theme.name,
                "version": version.version_string,
                "presets": version.presets or {},
            },
            is_latest=True,
            checksum=version.checksum or "marketplace",
            published_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        runtime_version = await self._version_repo.create(runtime_version)

        # 3) Customization preservation logic (preserved from the
        #    pre-Phase-3a behaviour): if the store had a published V3
        #    customization for the SAME marketplace theme previously,
        #    reuse it. Otherwise seed from the version's presets so the
        #    editor opens to the developer's canonical layout.
        existing_active = await self._store_theme_repo.get_active_for_store(store_id)
        preserved: dict | None = None

        # Only preserve a prior customization if the activated version can
        # actually render it — i.e. at least one of its section types is in the
        # version's section_schemas. A mismatched customization (theme switch,
        # or generic sections left by legacy seeding/snapshot restore) would
        # otherwise open the editor to un-editable sections that don't match
        # the bundle, so we reseed from presets instead. When the version ships
        # no section_schemas we can't judge → preserve (avoid a false reseed
        # that could drop real merchant edits; the activation service also
        # snapshots before any overwrite).
        _known_types = set(_coerce_section_schemas(version.section_schemas).keys())

        def _customization_is_renderable(cust: dict) -> bool:
            if not _known_types:
                return True
            for tpl in (cust.get("templates") or {}).values():
                if not isinstance(tpl, dict):
                    continue
                for sec in (tpl.get("sections") or {}).values():
                    if isinstance(sec, dict) and sec.get("type") in _known_types:
                        return True
            return False

        if (
            existing_active is not None
            and existing_active.theme_id == runtime_theme.id
            and isinstance(existing_active.customization_v3, dict)
            and existing_active.customization_v3.get("schema_version") == 3
            and existing_active.customization_v3.get("templates")
            and _customization_is_renderable(existing_active.customization_v3)
        ):
            preserved = existing_active.customization_v3

        if preserved is not None:
            seed = preserved
            logger.info(
                "marketplace_activate_restored_customization",
                extra={
                    "store_id": str(store_id),
                    "marketplace_theme_id": str(marketplace_theme_id),
                },
            )
        else:
            # The ExternalThemeMetadata allowlist refuses
            # ``http://localhost:5173/...`` in production mode (correct
            # — prod bundles must come from the configured CDN). Locally,
            # bon-younes ships from the dev vite server, so seeding a
            # brand-new store would explode on the strict allowlist.
            # Mirror the storefront's env convention: dev mode for
            # anything below production.
            from src.config import settings as _api_settings

            v3 = generate_initial_v3_customization(
                theme_id=str(runtime_theme.id),
                presets=version.presets,
                bundle_url=version.bundle_url,
                css_url=version.css_url,
                settings_schema=version.settings_schema,
                section_schemas=version.section_schemas,
                mode=(
                    "production"
                    if _api_settings.environment == "production"
                    else "development"
                ),
            )
            seed = v3.model_dump()

        # 4) Look up the tenant_id off the store. The activation service
        #    stamps it on snapshot and any newly-created store_themes row.
        if self._store_repo is None:
            raise ValueError(
                "MarketplaceService.activate_theme requires store_repo (for tenant_id)"
            )
        store = await self._store_repo.get_by_id(store_id)
        if store is None:
            raise ValueError("store not found")

        # 5) Hand off the swap to the shared activation service. It
        #    snapshots → deactivate_all → upsert_active → mirrors
        #    is_active to marketplace_theme_installations.
        from src.application.services.theme_activation_service import (
            ThemeActivationService,
        )
        from src.infrastructure.repositories.store_theme_snapshot_repository import (
            StoreThemeSnapshotRepository,
        )

        snapshot_repo = StoreThemeSnapshotRepository(self._store_theme_repo.session)
        activation_svc = ThemeActivationService(
            store_theme_repo=self._store_theme_repo,
            snapshot_repo=snapshot_repo,
            marketplace_repo=self._marketplace_repo,
        )
        await activation_svc.activate(
            store_id=store_id,
            tenant_id=store.tenant_id,
            theme_id=runtime_theme.id,
            theme_version_id=runtime_version.id,
            reason="marketplace-activate",
            marketplace_theme_id=marketplace_theme_id,
            seed_customization_v3=seed,
        )

        # Stamp `is_published=True` and `last_published_at=now` on the
        # store's V1 customization JSONB (`store.settings.customization`).
        # The merchant-hub Themes page renders "Published · <date>" off
        # this field; without an update on activate, a freshly-installed
        # marketplace theme shows the previous theme's stale publish date
        # which confuses merchants ("I just installed this — why does it
        # say last published yesterday?"). Best-effort — the activate
        # path mustn't fail if customization writes do (e.g. store_repo
        # not wired by the calling code path).
        if self._store_repo is not None:
            try:
                store = await self._store_repo.get_by_id(store_id)
                if store is not None:
                    settings_blob = dict(store.settings or {})
                    cust = dict(settings_blob.get("customization") or {})
                    cust["is_published"] = True
                    cust["last_published_at"] = datetime.now(UTC).isoformat()
                    settings_blob["customization"] = cust
                    store.settings = settings_blob
                    await self._store_repo.update(store)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "marketplace_activate_published_at_update_failed",
                    extra={
                        "store_id": str(store_id),
                        "error": str(exc),
                    },
                )

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

    async def list_upgradeable(self, store_id: UUID) -> list[dict[str, Any]]:
        """Return installed themes that have a newer published version.

        Compares each install's `marketplace_version_id` to the theme's
        `get_latest_published_version`. The merchant hub uses this to
        show an "Upgrade available" badge on the BYOT card. Each entry
        includes the installed + latest version strings so the UI can
        render a "v0.1.0 → v0.2.0" diff hint.
        """
        installs = await self._marketplace_repo.list_installations(store_id)
        out: list[dict[str, Any]] = []
        for inst in installs:
            if inst.uninstalled_at is not None:
                continue
            latest = await self._marketplace_repo.get_latest_published_version(
                inst.marketplace_theme_id
            )
            if not latest:
                continue
            if latest.id == inst.marketplace_version_id:
                continue
            installed_version = await self._marketplace_repo.get_version_by_id(
                inst.marketplace_version_id
            )
            out.append({
                "marketplace_theme_id": str(inst.marketplace_theme_id),
                "installation_id": str(inst.id),
                "installed_version_id": str(inst.marketplace_version_id),
                "installed_version_string": (
                    installed_version.version_string if installed_version else None
                ),
                "latest_version_id": str(latest.id),
                "latest_version_string": latest.version_string,
            })
        return out

    async def upgrade_theme(
        self,
        store_id: UUID,
        marketplace_theme_id: UUID,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Pin an existing install to the latest published version + re-activate.

        Customization migration is implicit: `activate_theme` checks
        `store_theme.customization_v3` first and uses it as the new
        draft. So upgrading from v0.1.0 → v0.2.0 keeps the merchant's
        prior settings; only when the prior payload is missing or
        malformed does it fall back to the new version's fresh presets.

        Forwards-compat caveat: if the new version dropped a section
        type the merchant was using, that section will render as
        "Section not found" until the merchant removes it. Future
        improvement: schema-aware migration that strips unknown
        section / setting keys.
        """
        installation = await self._marketplace_repo.get_installation(
            store_id, marketplace_theme_id
        )
        if not installation or installation.uninstalled_at is not None:
            raise ValueError("theme is not installed for this store")

        latest = await self._marketplace_repo.get_latest_published_version(
            marketplace_theme_id
        )
        if not latest:
            raise ValueError("no published version available")
        if latest.id == installation.marketplace_version_id:
            return {
                "marketplace_theme_id": str(marketplace_theme_id),
                "marketplace_version_id": str(installation.marketplace_version_id),
                "upgraded": False,
                "message": "Already on the latest version",
            }

        # Re-pin the installation to the latest version. We reuse
        # create_or_reactivate_installation with the new version_id.
        await self._marketplace_repo.create_or_reactivate_installation(
            store_id=store_id,
            marketplace_theme_id=marketplace_theme_id,
            marketplace_version_id=latest.id,
        )

        # Re-run activate to refresh the draft + revalidate. The
        # preservation branch in activate_theme prefers the merchant's
        # existing customization_v3 over fresh presets.
        await self.activate_theme(
            store_id=store_id,
            marketplace_theme_id=marketplace_theme_id,
            user_id=user_id,
        )

        return {
            "marketplace_theme_id": str(marketplace_theme_id),
            "marketplace_version_id": str(latest.id),
            "upgraded": True,
        }

    async def uninstall_theme(self, store_id: UUID, marketplace_theme_id: UUID) -> bool:
        """Soft-uninstall a marketplace theme.

        We mark the install row uninstalled + decrement the global
        install count, but DELIBERATELY leave the corresponding
        store_themes row (with its customization_v3 payload) in place.
        This way reinstalling later restores the merchant's prior
        settings rather than starting from the developer's fresh
        presets — the same merchant data they spent time configuring
        before they tried a different theme.

        The reactivation path in `activate_theme` reads
        store_theme.customization_v3 and prefers it over fresh
        presets when present. So preservation is implicit: don't
        delete the row.
        """
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
            # File 04 §8 — extend MarketplaceThemeCard/Detail with the
            # admin-curated metadata fields. Catalog list responses
            # surface ``author_name``, ``screenshots``, ``feature_tags``;
            # the detail endpoint additionally surfaces ``author_url``
            # and ``highlights`` (see get_theme_detail below).
            "author_name": getattr(t, "author_name", None),
            "screenshots": _screenshots_to_dicts(getattr(t, "screenshots", []) or []),
            "feature_tags": list(getattr(t, "feature_tags", []) or []),
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


def _admin_theme_dict(t: Any) -> dict[str, Any]:
    """Admin-facing serialisation. Includes the ``flags`` dict (the
    public catalog hides it). Stays as a free function so the
    MarketplaceService method can stay short."""
    return {
        "id": str(t.id),
        "slug": t.slug,
        "name": t.name,
        "status": (t.status.value if hasattr(t.status, "value") else str(t.status)),
        "price_cents": t.price_cents,
        "flags": dict(t.flags or {}),
        "install_count": t.install_count,
        "created_at": (t.created_at.isoformat() if t.created_at else ""),
    }


def _screenshots_to_dicts(items: list[Any]) -> list[dict[str, Any]]:
    """Coerce ``Screenshot`` entities (or already-dict screenshots) into
    the plain JSON shape Pydantic models expect."""
    out: list[dict[str, Any]] = []
    for s in items:
        if hasattr(s, "model_dump"):
            out.append(s.model_dump())
        elif isinstance(s, dict):
            out.append(s)
    return out


def _highlights_to_dicts(items: list[Any]) -> list[dict[str, Any]]:
    """Same shape coercion as ``_screenshots_to_dicts`` for highlights."""
    out: list[dict[str, Any]] = []
    for h in items:
        if hasattr(h, "model_dump"):
            out.append(h.model_dump())
        elif isinstance(h, dict):
            out.append(h)
    return out


def _admin_metadata_dict(t: Any) -> dict[str, Any]:
    """Admin metadata serialisation — response body for
    PATCH /marketplace/admin/themes/{id}.

    Same shape as ``AdminThemeMetadataResponse`` in the schemas module.
    Stays here (alongside ``_admin_theme_dict``) so MarketplaceService
    doesn't have to import the API schema layer.
    """
    return {
        "id": str(t.id),
        "slug": t.slug,
        "name": t.name,
        "status": (t.status.value if hasattr(t.status, "value") else str(t.status)),
        "price_cents": t.price_cents,
        "currency": t.currency,
        "description": t.description,
        "short_description": t.short_description,
        "thumbnail_url": t.thumbnail_url,
        "demo_store_url": t.demo_store_url,
        "author_name": getattr(t, "author_name", None),
        "author_url": getattr(t, "author_url", None),
        "screenshots": _screenshots_to_dicts(getattr(t, "screenshots", []) or []),
        "highlights": _highlights_to_dicts(getattr(t, "highlights", []) or []),
        "feature_tags": list(getattr(t, "feature_tags", []) or []),
        "category": t.category,
        "tags": list(t.tags or []),
        "supported_languages": list(t.supported_languages or []),
        "flags": dict(t.flags or {}),
        "install_count": t.install_count,
        "created_at": (t.created_at.isoformat() if t.created_at else ""),
    }
