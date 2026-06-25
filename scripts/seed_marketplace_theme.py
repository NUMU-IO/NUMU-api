"""Seed a built V3 theme into the MARKETPLACE catalog.

Companion to scripts/seed_numu_theme.py (which seeds the themes/theme_versions
registry the storefront SSR reads). This seeds the public marketplace catalog:
  * public.marketplace_themes          — the listing (status=published, flags
                                          catalog_visible/installable/activatable)
  * public.marketplace_theme_versions  — an approved version pointing at the R2
                                          bundle

Idempotent — matched by slug; re-running updates the listing + the version row
in place. Uses core __table__ statements (no ORM mapper config), like
seed_numu_theme.py / seed_themes.py.

Usage:
    THEME_DIR=/path/to/theme/dist \
    THEME_BUNDLE_BASE=https://cdn.numueg.app \
    [DEVELOPER_USER_ID=<uuid>] \
    python scripts/seed_marketplace_theme.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.database import AsyncSessionLocal
from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeModel,
    MarketplaceThemeVersionModel,
)

DEFAULT_DIST = Path(
    os.environ.get("THEME_DIR", "C:/Users/Yahia/NUMU/numu-theme-magic/dist")
)
BUNDLE_BASE = os.environ.get("THEME_BUNDLE_BASE", "https://cdn.numueg.app")

# Visible + installable + activatable in the public catalog. `{}` ⇒ invisible.
PUBLISHED_FLAGS = {
    "catalog_visible": True,
    "installable": True,
    "activatable": True,
}


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


async def _resolve_developer_id(session) -> str:
    explicit = os.environ.get("DEVELOPER_USER_ID")
    if explicit:
        return explicit
    for query in (
        "SELECT id FROM public.users WHERE email = 'admin@numueg.app' "
        "ORDER BY created_at LIMIT 1",
        "SELECT id FROM public.users ORDER BY created_at LIMIT 1",
    ):
        row = (await session.execute(text(query))).first()
        if row:
            return str(row[0])
    raise SystemExit(
        "No user found for marketplace developer_id — set DEVELOPER_USER_ID."
    )


async def seed() -> None:
    dist = DEFAULT_DIST
    manifest = _load_json(dist / "theme.json", None)
    if manifest is None:
        raise SystemExit(
            f"No theme.json at {dist} — build the theme first (numu-theme build)."
        )

    slug = manifest["id"]
    name = manifest.get("name", slug)
    if isinstance(name, dict):
        name = name.get("en") or name.get("default") or slug
    version = manifest.get("version", "0.1.0")
    description = manifest.get("description")
    tags = manifest.get("tags") if isinstance(manifest.get("tags"), list) else []
    presets = manifest.get("presets") or {}
    settings_schema = _load_json(dist / "settings_schema.json", [])
    sections = _load_json(dist / "sections.json", None) or {}
    section_schemas = (
        sections.get("sections", sections) if isinstance(sections, dict) else {}
    )

    theme_js = dist / "theme.js"
    checksum = (
        hashlib.sha256(theme_js.read_bytes()).hexdigest()
        if theme_js.exists()
        else hashlib.sha256(slug.encode()).hexdigest()
    )

    bundle_url = f"{BUNDLE_BASE}/{slug}/{version}/theme.js"
    css_url = f"{BUNDLE_BASE}/{slug}/{version}/theme.css"
    now = datetime.now(UTC)

    themes = MarketplaceThemeModel.__table__
    versions = MarketplaceThemeVersionModel.__table__

    async with AsyncSessionLocal() as session:
        developer_id = await _resolve_developer_id(session)

        row = (
            await session.execute(themes.select().where(themes.c.slug == slug))
        ).first()

        if row:
            theme_id = row.id
            await session.execute(
                themes.update()
                .where(themes.c.id == theme_id)
                .values(
                    name=name,
                    description=description,
                    tags=tags,
                    status="published",
                    flags=PUBLISHED_FLAGS,
                    author_name="NUMU",
                    updated_at=now,
                )
            )
            print(f"  [update] marketplace theme {slug} ({theme_id})")
        else:
            theme_id = uuid4()
            await session.execute(
                themes.insert().values(
                    id=theme_id,
                    developer_id=developer_id,
                    name=name,
                    slug=slug,
                    description=description,
                    price_cents=0,
                    currency="EGP",
                    status="published",
                    tags=tags,
                    supported_languages=["en", "ar"],
                    supported_features={"darkMode": False, "rtl": True},
                    flags=PUBLISHED_FLAGS,
                    author_name="NUMU",
                    created_at=now,
                    updated_at=now,
                )
            )
            print(f"  [seed] marketplace theme {slug} ({theme_id})")

        vrow = (
            await session.execute(
                versions.select().where(
                    versions.c.theme_id == theme_id,
                    versions.c.version_string == version,
                )
            )
        ).first()

        version_values = {
            "bundle_url": bundle_url,
            "css_url": css_url,
            "settings_schema": settings_schema,
            "section_schemas": section_schemas,
            "presets": presets,
            # `published` (not `approved`): install/update reads the latest
            # PUBLISHED version, so this is what surfaces as an available update.
            "status": "published",
            "checksum": checksum,
        }

        if vrow:
            await session.execute(
                versions.update()
                .where(versions.c.id == vrow.id)
                .values(**version_values)
            )
            print(f"  [update] marketplace version {version} ({vrow.id})")
        else:
            await session.execute(
                versions.insert().values(
                    id=uuid4(),
                    theme_id=theme_id,
                    version_string=version,
                    release_notes="Seed — V3 theme external bundle",
                    created_at=now,
                    **version_values,
                )
            )
            print(f"  [seed] marketplace version {version}")

        await session.commit()
        print(f"\nDone. marketplace '{name}' (slug={slug}, v{version}) -> {bundle_url}")


if __name__ == "__main__":
    print("Seeding marketplace theme...\n")
    asyncio.run(seed())
