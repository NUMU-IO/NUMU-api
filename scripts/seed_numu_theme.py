"""Seed the external "numu theme" (numu-theme-magic) into the test DB.

Registers the BYOT theme built at numu-theme-magic/dist as an `external`
ThemeModel + ThemeVersionModel, reading the real manifest / schemas / checksum
from the built bundle. Idempotent — matched by slug; re-running updates the
latest version row in place.

Uses core `__table__` statements (not the ORM session) so it doesn't trigger
full mapper configuration — same approach as scripts/seed_themes.py.

Usage:
    python scripts/seed_numu_theme.py
    THEME_DIR=/path/to/theme/dist python scripts/seed_numu_theme.py
    THEME_BUNDLE_BASE=https://r2.numueg.app/themes python scripts/seed_numu_theme.py

The DB row points `bundle_url` at the R2/CDN convention
(`{THEME_BUNDLE_BASE}/{slug}/{version}/theme.js`). The bundle FILE itself is
uploaded to R2 by the build pipeline (`numu-theme install`/`submit`); this
script only seeds the registry rows. Host must be in NUMU_BYOT_BUNDLE_HOSTS.
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

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.database import AsyncSessionLocal
from src.infrastructure.database.models import ThemeModel, ThemeVersionModel

DEFAULT_DIST = Path(
    os.environ.get("THEME_DIR", "C:/Users/Yahia/NUMU/numu-theme-magic/dist")
)
BUNDLE_BASE = os.environ.get("THEME_BUNDLE_BASE", "https://r2.numueg.app/themes")


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


async def seed() -> None:
    dist = DEFAULT_DIST
    manifest = _load_json(dist / "theme.json", None)
    if manifest is None:
        raise SystemExit(
            f"No theme.json at {dist} — build the theme first (numu-theme build)."
        )

    slug = manifest["id"]
    name = manifest.get("name", slug)
    version = manifest.get("version", "0.1.0")
    settings_schema = _load_json(dist / "settings_schema.json", [])
    section_schemas = _load_json(dist / "sections.json", None)

    theme_js = dist / "theme.js"
    checksum = (
        hashlib.sha256(theme_js.read_bytes()).hexdigest()
        if theme_js.exists()
        else hashlib.sha256(slug.encode()).hexdigest()
    )

    bundle_url = f"{BUNDLE_BASE}/{slug}/{version}/theme.js"
    css_url = f"{BUNDLE_BASE}/{slug}/{version}/theme.css"
    now = datetime.now(UTC)

    themes = ThemeModel.__table__
    versions = ThemeVersionModel.__table__

    async with AsyncSessionLocal() as session:
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
                    description=manifest.get("description"),
                    settings_schema=settings_schema,
                    section_schemas=section_schemas,
                    updated_at=now,
                )
            )
            print(f"  [update] theme {slug} ({theme_id})")
        else:
            theme_id = uuid4()
            await session.execute(
                themes.insert().values(
                    id=theme_id,
                    name=name,
                    slug=slug,
                    description=manifest.get("description"),
                    author=manifest.get("author", "NUMU"),
                    type="external",
                    is_public=True,
                    status="published",
                    settings_schema=settings_schema,
                    section_schemas=section_schemas,
                    supported_features={"darkMode": False, "rtl": True},
                    created_at=now,
                    updated_at=now,
                )
            )
            print(f"  [seed] theme {slug} ({theme_id})")

        # Demote any previous latest, then upsert this version.
        await session.execute(
            versions.update()
            .where(versions.c.theme_id == theme_id)
            .values(is_latest=False)
        )

        vrow = (
            await session.execute(
                versions.select().where(
                    versions.c.theme_id == theme_id,
                    versions.c.version == version,
                )
            )
        ).first()

        version_values = {
            "bundle_url": bundle_url,
            "css_url": css_url,
            "manifest": manifest,
            "checksum": checksum,
            "is_latest": True,
            "published_at": now,
            "updated_at": now,
        }

        if vrow:
            await session.execute(
                versions.update()
                .where(versions.c.id == vrow.id)
                .values(**version_values)
            )
            print(f"  [update] version {version} ({vrow.id})")
        else:
            version_id = uuid4()
            await session.execute(
                versions.insert().values(
                    id=version_id,
                    theme_id=theme_id,
                    version=version,
                    changelog="Seed — numu theme (numu-theme-magic) external bundle",
                    created_at=now,
                    **version_values,
                )
            )
            print(f"  [seed] version {version} ({version_id})")

        await session.commit()
        print(f"\nDone. '{name}' (slug={slug}, v{version}) -> bundle {bundle_url}")


if __name__ == "__main__":
    print("Seeding external theme 'numu theme'...\n")
    asyncio.run(seed())
