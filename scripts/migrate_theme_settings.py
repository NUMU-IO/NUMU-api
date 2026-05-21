"""Migrate existing stores.theme_settings JSONB -> store_themes rows.

For each store that has a theme_settings.theme.base_theme value:
1. Look up the matching theme by slug in the themes table
2. Resolve the latest version for that theme
3. Create a store_themes row (is_active=True, customization = current theme_settings)

Prerequisites:
  - Run the Alembic migration that creates theme tables first
  - Run seed_themes.py to populate the built-in themes catalog

Idempotent — skips stores that already have a store_themes row.

Usage:
    python scripts/migrate_theme_settings.py [--dry-run]
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from src.infrastructure.database import AsyncSessionLocal
from src.infrastructure.database.models import (
    StoreModel,
    StoreThemeModel,
    ThemeModel,
    ThemeVersionModel,
)


async def migrate_theme_settings(dry_run: bool = False) -> None:
    """Read stores.theme_settings and create store_themes rows."""
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        # Pre-load all themes by slug for fast lookup
        themes_result = await session.execute(
            select(ThemeModel).where(ThemeModel.type == "internal")
        )
        themes_by_slug: dict[str, ThemeModel] = {
            t.slug: t for t in themes_result.scalars().all()
        }
        print(f"Loaded {len(themes_by_slug)} themes from catalog")

        if not themes_by_slug:
            print("ERROR: No themes found. Run seed_themes.py first.")
            return

        # Pre-load latest versions by theme_id
        versions_result = await session.execute(
            select(ThemeVersionModel).where(ThemeVersionModel.is_latest == True)  # noqa: E712
        )
        latest_versions: dict[str, ThemeVersionModel] = {
            str(v.theme_id): v for v in versions_result.scalars().all()
        }
        print(f"Loaded {len(latest_versions)} latest versions")

        # Load all stores with their theme_settings
        stores_result = await session.execute(
            select(StoreModel).where(StoreModel.theme_settings.isnot(None))
        )
        stores = stores_result.scalars().all()
        print(f"Found {len(stores)} stores with theme_settings\n")

        migrated = 0
        skipped_existing = 0
        skipped_no_theme = 0
        skipped_no_match = 0

        for store in stores:
            store_id = str(store.id)
            tenant_id = str(store.tenant_id) if store.tenant_id else None

            if not tenant_id:
                print(f"  [skip] store {store_id} ({store.name}) — no tenant_id")
                skipped_no_theme += 1
                continue

            # Check if this store already has a store_themes row
            existing = await session.execute(
                select(StoreThemeModel)
                .where(StoreThemeModel.store_id == store.id)
                .limit(1)
            )
            if existing.first():
                print(f"  [skip] store {store_id} ({store.name}) — already migrated")
                skipped_existing += 1
                continue

            # Extract base_theme slug from theme_settings JSONB
            theme_settings = store.theme_settings or {}
            theme_block = theme_settings.get("theme", {})
            base_theme = theme_block.get("base_theme")

            if not base_theme:
                print(
                    f"  [skip] store {store_id} ({store.name}) — no base_theme in theme_settings"
                )
                skipped_no_theme += 1
                continue

            # Look up the theme in the catalog
            theme = themes_by_slug.get(base_theme)
            if not theme:
                print(
                    f"  [warn] store {store_id} ({store.name}) — base_theme '{base_theme}' not in catalog"
                )
                skipped_no_match += 1
                continue

            # Resolve latest version
            version = latest_versions.get(str(theme.id))
            if not version:
                print(
                    f"  [warn] store {store_id} ({store.name}) — no version for theme '{base_theme}'"
                )
                skipped_no_match += 1
                continue

            # Build customization from the full theme_settings (minus the "theme" internal key)
            customization = dict(theme_settings)

            if not dry_run:
                installation = StoreThemeModel(
                    id=uuid4(),
                    store_id=store.id,
                    tenant_id=store.tenant_id,
                    theme_id=theme.id,
                    theme_version_id=version.id,
                    is_active=True,
                    customization=customization,
                    draft_customization={},
                    installed_at=now,
                    activated_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(installation)

            action = "would migrate" if dry_run else "migrated"
            print(
                f"  [{action}] store {store_id} ({store.name}) -> theme '{base_theme}'"
            )
            migrated += 1

        if not dry_run:
            await session.commit()

        print(f"\n{'DRY RUN — ' if dry_run else ''}Summary:")
        print(f"  Migrated:           {migrated}")
        print(f"  Skipped (existing): {skipped_existing}")
        print(f"  Skipped (no theme): {skipped_no_theme}")
        print(f"  Skipped (no match): {skipped_no_match}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN mode — no changes will be written\n")
    else:
        print("Migrating store theme_settings -> store_themes...\n")
    asyncio.run(migrate_theme_settings(dry_run=dry_run))
