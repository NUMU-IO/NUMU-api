"""Seed the 12 built-in NUMU themes into the themes + theme_versions tables.

Usage:
    python scripts/seed_themes.py

Idempotent — skips themes that already exist (matched by slug).
"""

import asyncio
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.database import AsyncSessionLocal
from src.infrastructure.database.models import ThemeModel, ThemeVersionModel

# ── Built-in theme definitions ────────────────────────────────────────────────
# Sourced from numu-egyptian-bazaar/src/themes/*/index.ts manifests.

BUILT_IN_THEMES = [
    {
        "slug": "modern",
        "name": "Modern Minimal",
        "description": "Clean, minimal teal aesthetic",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["light", "minimal", "clean"],
        "layout": "default",
    },
    {
        "slug": "editorial",
        "name": "Editorial",
        "description": "Bold editorial fashion theme with oversized typography and dramatic green palette",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["bold", "fashion", "editorial", "green", "typography"],
        "layout": "editorial",
    },
    {
        "slug": "boutique",
        "name": "Vibrant Boutique",
        "description": "Vibrant pink/magenta fashion-forward aesthetic",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["light", "vibrant", "fashion"],
        "layout": "default",
    },
    {
        "slug": "elegant",
        "name": "Classic Elegant",
        "description": "Rich warm brown classic elegance",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["light", "classic", "warm"],
        "layout": "default",
    },
    {
        "slug": "neo-brutalism",
        "name": "Neo Brutalism",
        "description": "Bold, raw, unapologetic design with thick borders, hard shadows, and neon accents",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["bold", "raw", "neon", "playful", "brutalist"],
        "layout": "neo-brutalism",
    },
    {
        "slug": "skeuomorphic",
        "name": "Skeuomorphic",
        "description": "Physical, tactile 3D interface design",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["light", "3d", "tactile", "physical"],
        "layout": "skeuomorphic",
    },
    {
        "slug": "luxury-minimal",
        "name": "Luxury Minimal",
        "description": "Ultra-clean luxury minimalist theme with refined typography and understated elegance",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["luxury", "minimal", "clean", "elegant", "light"],
        "layout": "luxury-minimal",
    },
    {
        "slug": "tech-wave",
        "name": "Tech Wave",
        "description": "Futuristic dark theme with neon accents, glassmorphism, and wave effects",
        "supported_features": {"darkMode": True, "rtl": True},
        "tags": ["dark", "futuristic", "neon", "tech"],
        "layout": "default",
    },
    {
        "slug": "empire",
        "name": "Empire",
        "description": "Premium editorial e-commerce with monochromatic palette, wide display typography, and content-first design",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["editorial", "monochrome", "premium", "clean", "flat"],
        "layout": "empire",
    },
    {
        "slug": "kick-game",
        "name": "Kick Game",
        "description": "Warm minimalist luxury streetwear — cream backgrounds, dense editorial grid, zero ornamentation, sneakers-as-art",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["luxury", "streetwear", "minimal", "warm", "editorial", "sneakers"],
        "layout": "kick-game",
    },
    {
        "slug": "street",
        "name": "Street Vibes",
        "description": "Bold urban streetwear — vibrant yellow, topographic lines, chunky type",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["dark", "bold", "urban", "streetwear"],
        "layout": "street",
    },
    {
        "slug": "rabbitsocks",
        "name": "RabbitSocks",
        "description": "Luxury minimalism — quiet luxury aesthetic with serif italic headlines, generous whitespace, and editorial photography",
        "supported_features": {"darkMode": True, "rtl": True},
        "tags": ["luxury", "editorial", "minimal", "serif", "quiet-luxury"],
        "layout": "rabbitsocks",
    },
    {
        "slug": "gilded-glamour-boutique",
        "name": "Gilded Glamour Boutique",
        "description": "A bold, gold-accented luxury fashion theme with parallax hero, scroll-fill text animations, and curated vertical layouts",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["luxury", "gold", "fashion", "editorial", "parallax", "minimal"],
        "layout": "luxury-minimal",
        "author": "Saw Saw Atelier",
    },
    {
        "slug": "vionne",
        "name": "Vionne",
        "description": "Refined grayscale storefront for modest fashion. Crisp typography, slow fade slideshow, draggable before/after, and motion-led product cards.",
        "supported_features": {"darkMode": False, "rtl": True},
        "tags": ["hijab", "modest", "fashion", "ecommerce", "minimal", "grayscale"],
        "layout": "vionne",
    },
]

THEME_VERSION = "1.0.0"


def _placeholder_checksum(slug: str) -> str:
    """Generate a deterministic checksum placeholder for a built-in theme.

    Built-in themes are compiled into the Next.js app, not served from R2,
    so this is a placeholder. Replaced with a real SHA-256 when the theme
    is built as an external bundle.
    """
    return hashlib.sha256(f"numu-builtin-{slug}-{THEME_VERSION}".encode()).hexdigest()


async def seed_themes() -> None:
    """Insert built-in themes and their v1.0.0 versions."""
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        created_themes = 0
        skipped_themes = 0

        for theme_def in BUILT_IN_THEMES:
            slug = theme_def["slug"]

            # Idempotent: skip if already exists
            result = await session.execute(
                ThemeModel.__table__.select().where(ThemeModel.slug == slug)
            )
            if result.first():
                print(f"  [skip] {slug} — already exists")
                skipped_themes += 1
                continue

            # Create theme
            theme_id = uuid4()
            theme = ThemeModel(
                id=theme_id,
                name=theme_def["name"],
                slug=slug,
                description=theme_def["description"],
                author=theme_def.get("author", "NUMU"),
                type="internal",
                is_public=True,
                status="published",
                settings_schema={},
                section_schemas=None,
                supported_features=theme_def["supported_features"],
                created_at=now,
                updated_at=now,
            )
            session.add(theme)

            # Create initial version (v1.0.0)
            version_id = uuid4()
            checksum = _placeholder_checksum(slug)
            manifest = {
                "id": slug,
                "name": theme_def["name"],
                "description": theme_def["description"],
                "version": THEME_VERSION,
                "author": theme_def.get("author", "NUMU"),
                "layout": theme_def["layout"],
                "tags": theme_def["tags"],
                "supports": theme_def["supported_features"],
            }

            version = ThemeVersionModel(
                id=version_id,
                theme_id=theme_id,
                version=THEME_VERSION,
                bundle_url=f"builtin://{slug}",  # Internal themes are compiled into the Next.js app
                css_url=f"builtin://{slug}/styles.css",
                manifest=manifest,
                changelog="Initial release — built-in NUMU theme",
                is_latest=True,
                checksum=checksum,
                published_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(version)

            print(f"  [seed] {slug} (theme={theme_id}, version={version_id})")
            created_themes += 1

        await session.commit()
        print(
            f"\nDone. Created {created_themes} themes, "
            f"skipped {skipped_themes} (already existed)."
        )


if __name__ == "__main__":
    print("Seeding built-in themes...\n")
    asyncio.run(seed_themes())
