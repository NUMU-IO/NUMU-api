"""Platform default theme service (Session A 2026-05-27, file 04 §5).

Owns reads/writes of the ``platform_config.default_marketplace_theme_id``
column. The column lives on a generic key-value table; this service
defines the convention that the **row keyed** ``platform_default_theme``
is the canonical holder of the value (the column is technically present
on every row, but only this row's value is read).

Three operations:

  * ``get_default_theme_id()`` — return the UUID or ``None``. Cheap;
    used on every store-creation request that would seed the default.
  * ``update_default_theme(theme_id)`` — admin sets/clears the default.
    Validates that ``theme_id`` is a published, installable marketplace
    theme before writing. Passing ``None`` clears the default.
  * ``get_default_theme_summary()`` — fetch enough metadata about the
    current default to render an admin UI badge (name, slug, status).

Why not stash the UUID inside ``platform_config.value`` JSONB like
``meta_credentials`` / ``platform_settings`` do? Because we want the
FK + index from migration 20260527_010000, and because hiding a UUID
inside JSON is exactly the kind of thing that makes future
data-integrity migrations painful.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.marketplace_theme import MarketplaceThemeStatus
from src.core.exceptions import ValidationError
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)

PLATFORM_DEFAULT_THEME_KEY = "platform_default_theme"


class PlatformDefaultThemeService:
    """Single source of truth for the platform-wide default theme setting."""

    def __init__(
        self,
        db: AsyncSession,
        marketplace_repo: MarketplaceRepository,
    ) -> None:
        self._db = db
        self._marketplace_repo = marketplace_repo

    # ── Reads ──────────────────────────────────────────────────────────

    async def get_default_theme_id(self) -> UUID | None:
        """Return the configured default theme UUID, or None."""
        row = await self._get_canonical_row()
        return row.default_marketplace_theme_id if row else None

    async def get_default_theme_summary(self) -> dict[str, str | None] | None:
        """Return ``{id, slug, name, status}`` for the configured default,
        or ``None`` if no default is set. Useful for admin UI."""
        theme_id = await self.get_default_theme_id()
        if theme_id is None:
            return None
        theme = await self._marketplace_repo.get_theme_by_id(theme_id)
        if theme is None:
            # FK should have prevented this; treat as if no default
            return None
        return {
            "id": str(theme.id),
            "slug": theme.slug,
            "name": theme.name,
            "status": (
                theme.status.value
                if hasattr(theme.status, "value")
                else str(theme.status)
            ),
        }

    # ── Writes ─────────────────────────────────────────────────────────

    async def update_default_theme(self, theme_id: UUID | None) -> UUID | None:
        """Set or clear the platform default theme.

        When ``theme_id`` is not None, validates:
          1. theme exists in ``marketplace_themes``
          2. theme.status == 'published'
          3. theme.flags.installable is truthy (you can't default to an
             uninstallable theme — new stores would just fail to seed)

        Raises:
            ValidationError: any check fails. Caller is expected to map
            this to HTTP 400.

        Returns the new value (echo of ``theme_id`` or ``None``).

        **sawsaw + rabbit are not cascade-affected** — the platform
        default is only read on store-creation. Existing stores keep
        whatever ``theme_settings`` they already have.
        """
        if theme_id is not None:
            theme = await self._marketplace_repo.get_theme_by_id(theme_id)
            if theme is None:
                raise ValidationError(
                    f"theme {theme_id} not found in marketplace_themes"
                )
            if theme.status != MarketplaceThemeStatus.PUBLISHED:
                raise ValidationError(
                    "Platform default theme must be published "
                    f"(current status: {theme.status.value if hasattr(theme.status, 'value') else theme.status})"
                )
            flags = dict(theme.flags or {})
            if not flags.get("installable"):
                raise ValidationError(
                    "Platform default theme must have flags.installable=true "
                    "(merchants would fail to install it)"
                )

        row = await self._get_or_create_canonical_row()
        row.default_marketplace_theme_id = theme_id
        await self._db.commit()
        await self._db.refresh(row)
        return row.default_marketplace_theme_id

    # ── Internals ──────────────────────────────────────────────────────

    async def _get_canonical_row(self) -> PlatformConfigModel | None:
        result = await self._db.execute(
            select(PlatformConfigModel).where(
                PlatformConfigModel.key == PLATFORM_DEFAULT_THEME_KEY
            )
        )
        return result.scalar_one_or_none()

    async def _get_or_create_canonical_row(self) -> PlatformConfigModel:
        """Race-safe upsert of the canonical row."""
        row = await self._get_canonical_row()
        if row is not None:
            return row

        stmt = (
            pg_insert(PlatformConfigModel)
            .values(
                key=PLATFORM_DEFAULT_THEME_KEY,
                value={},
                description=(
                    "Platform-wide default theme for newly-created stores "
                    "(populated by /api/v1/admin/platform-config PATCH). "
                    "NULL = legacy V2 fallback. See file 04 §5."
                ),
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
        await self._db.execute(stmt)
        await self._db.commit()

        row = await self._get_canonical_row()
        if row is None:
            # Should be impossible after a successful insert + commit
            raise RuntimeError(
                f"failed to upsert platform_config row {PLATFORM_DEFAULT_THEME_KEY!r}"
            )
        return row
