"""Activate a locally-built V3 (BYOT) theme on a store for local QA.

LOCAL DEV ONLY. Points a store's active theme at a dev bundle you serve over
HTTP (``http://127.0.0.1:<port>/theme.{js,css}``) and syncs every table the
storefront SSR + the V3 customizer read, so the editor and storefront agree on
which theme is active.

Why this exists (and the bug it fixes)
--------------------------------------
The platform tracks the active theme in TWO mirrored tables:
  * ``store_themes.is_active``                  — the canonical active pointer
  * ``marketplace_theme_installations.is_active`` — the marketplace-catalog mirror
Production activation funnels through ``ThemeActivationService.activate`` which
flips BOTH atomically (see ``application/services/theme_activation_service.py``).
An earlier ad-hoc dev activation helper wrote only ``store_themes`` and left
``marketplace_theme_installations`` pointing at the PREVIOUSLY active theme. The
two tables then disagreed, and the hub/editor reconciled the active theme back to
the stale marketplace install — so the previous (wrong) theme "returned" on every
editor page-switch / reload. This script keeps BOTH tables consistent (step 6),
matching the service's invariant, so that can't happen for any theme.

Idempotent: re-running updates the same dev version + store_theme row.

Usage::

    # serve the built bundle first, e.g.:
    #   python -m http.server 5173 --bind 127.0.0.1 --directory <theme>/dist
    python scripts/dev_activate_v3_theme.py \
        --slug luxury-minimal-v3 \
        --dist /path/to/<theme>/dist \
        --store testlocal --port 5173

    # reactivate a previous store_theme row (e.g. to restore the prior theme):
    python scripts/dev_activate_v3_theme.py --restore --store testlocal \
        --store-theme-id <uuid>

Safety: refuses to run against a ``staging`` or ``production`` environment
(the ``-staging`` droplet stack IS production) unless ``--force`` is passed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text  # noqa: E402

from src.config.settings import settings  # noqa: E402
from src.infrastructure.database import AsyncSessionLocal  # noqa: E402

DEV_VERSION_SUFFIX = "+devlocal"


def _guard(force: bool) -> None:
    """Refuse to mutate a non-local environment unless explicitly forced."""
    env = settings.environment
    if env in ("staging", "production") and not force:
        raise SystemExit(
            f"Refusing to run against environment={env!r} "
            f"(the -staging stack IS production). This is a LOCAL DEV tool. "
            f"Re-run with --force only if you are certain."
        )


async def activate(slug: str, dist: str, store_sub: str, port: int) -> None:
    dist_path = Path(dist)
    manifest = json.loads((dist_path / "manifest.json").read_text(encoding="utf-8"))
    settings_schema = manifest.get("settings_schema", [])
    section_schemas = manifest.get("section_schemas", {})
    base_version = manifest.get("version", "0.0.0")
    dev_version = base_version + DEV_VERSION_SUFFIX
    bundle_url = f"http://127.0.0.1:{port}/theme.js"
    css_url = f"http://127.0.0.1:{port}/theme.css"
    external = {"mode": "development", "bundle_url": bundle_url, "css_url": css_url}
    js_bytes = (dist_path / "theme.js").read_bytes()
    checksum = hashlib.sha256(js_bytes).hexdigest()
    size_bytes = len(js_bytes)
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as s:
        theme_id = (
            await s.execute(
                text("SELECT id FROM themes WHERE slug=:slug"), {"slug": slug}
            )
        ).scalar()
        if not theme_id:
            raise SystemExit(f"theme slug not found: {slug}")
        store = (
            await s.execute(
                text("SELECT id, tenant_id FROM stores WHERE subdomain=:sub"),
                {"sub": store_sub},
            )
        ).first()
        if not store:
            raise SystemExit(f"store not found: {store_sub}")
        store_id, tenant_id = store.id, store.tenant_id

        # 1) Sync editor schemas from the freshly-built manifest. The V3
        #    customizer reads themes.settings_schema / section_schemas — re-sync
        #    on every rebuild or the editor shows stale/empty forms.
        await s.execute(
            text(
                "UPDATE themes SET settings_schema=CAST(:ss AS jsonb), "
                "section_schemas=CAST(:sec AS jsonb), updated_at=:now WHERE id=:tid"
            ),
            {
                "ss": json.dumps(settings_schema),
                "sec": json.dumps(section_schemas),
                "now": now,
                "tid": theme_id,
            },
        )

        # 2) Upsert the dev theme_version pointing at the locally-served bundle.
        ver_id = (
            await s.execute(
                text(
                    "SELECT id FROM theme_versions WHERE theme_id=:tid AND version=:ver"
                ),
                {"tid": theme_id, "ver": dev_version},
            )
        ).scalar()
        ver_vals = {
            "bundle": bundle_url,
            "css": css_url,
            "manifest": json.dumps(manifest),
            "checksum": checksum,
            "size": size_bytes,
            "now": now,
        }
        if ver_id:
            await s.execute(
                text(
                    "UPDATE theme_versions SET bundle_url=:bundle, css_url=:css, "
                    "manifest=CAST(:manifest AS jsonb), checksum=:checksum, "
                    "size_bytes=:size, updated_at=:now WHERE id=:vid"
                ),
                {**ver_vals, "vid": ver_id},
            )
        else:
            ver_id = (
                await s.execute(
                    text(
                        "INSERT INTO theme_versions "
                        "(id, theme_id, version, bundle_url, css_url, manifest, checksum, "
                        " size_bytes, is_latest, changelog, published_at, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), :tid, :ver, :bundle, :css, "
                        " CAST(:manifest AS jsonb), :checksum, :size, false, 'dev local', "
                        " :now, :now, :now) RETURNING id"
                    ),
                    {**ver_vals, "tid": theme_id, "ver": dev_version},
                )
            ).scalar()

        # 3) A valid ThemeSettingsV3 draft. RESET templates + section_groups to {}
        #    so the bundle's OWN built-in preset (header first / footer last) is
        #    used — preserving a prior theme's templates pins a stale layout. The
        #    editor refuses to load unless the draft has schema_version===3.
        prev_cv3 = (
            await s.execute(
                text(
                    "SELECT customization_v3 FROM store_themes WHERE store_id=:sid "
                    "AND theme_id=:tid"
                ),
                {"sid": store_id, "tid": theme_id},
            )
        ).scalar()
        prev: dict[str, Any] = {}
        if isinstance(prev_cv3, str):
            prev = json.loads(prev_cv3)
        elif isinstance(prev_cv3, dict):
            prev = prev_cv3
        draft = json.dumps({
            "schema_version": 3,
            "theme_id": str(theme_id),
            "external_theme": external,
            "templates": {},
            "section_groups": {},
            "global_settings": prev.get("global_settings") or {},
        })

        # 4) Flip the active store_themes row. Deactivate ALL first (partial-unique
        #    active index = one active per store), then upsert the target active.
        await s.execute(
            text(
                "UPDATE store_themes SET is_active=false, updated_at=:now WHERE store_id=:sid"
            ),
            {"now": now, "sid": store_id},
        )
        existing = (
            await s.execute(
                text(
                    "SELECT id FROM store_themes WHERE store_id=:sid AND theme_id=:tid"
                ),
                {"sid": store_id, "tid": theme_id},
            )
        ).scalar()
        if existing:
            await s.execute(
                text(
                    "UPDATE store_themes SET theme_version_id=:vid, is_active=true, "
                    "customization_v3=CAST(:draft AS jsonb), "
                    "draft_customization_v3=CAST(:draft AS jsonb), "
                    "activated_at=:now, updated_at=:now WHERE id=:id"
                ),
                {"vid": ver_id, "draft": draft, "now": now, "id": existing},
            )
            store_theme_id = existing
        else:
            store_theme_id = (
                await s.execute(
                    text(
                        "INSERT INTO store_themes "
                        "(id, tenant_id, store_id, theme_id, theme_version_id, is_active, "
                        " customization_v3, draft_customization_v3, installed_at, activated_at, "
                        " created_at, updated_at) "
                        "VALUES (gen_random_uuid(), :ten, :sid, :tid, :vid, true, "
                        " CAST(:draft AS jsonb), CAST(:draft AS jsonb), :now, :now, :now, :now) "
                        "RETURNING id"
                    ),
                    {
                        "ten": tenant_id,
                        "sid": store_id,
                        "tid": theme_id,
                        "vid": ver_id,
                        "draft": draft,
                        "now": now,
                    },
                )
            ).scalar()

        # 5) Denormalize to stores.theme_settings.external_theme (read by the SSR
        #    resolution fallback + the editor live-preview iframe).
        await s.execute(
            text(
                "UPDATE stores SET theme_settings = jsonb_set("
                "COALESCE(theme_settings,'{}'::jsonb), '{external_theme}', "
                "CAST(:ext AS jsonb), true), updated_at=:now WHERE id=:sid"
            ),
            {"ext": json.dumps(external), "now": now, "sid": store_id},
        )

        # 6) ENGINE INVARIANT — mirror marketplace_theme_installations to match
        #    store_themes (exactly what ThemeActivationService does). WITHOUT this
        #    the two tables disagree and the hub/editor reconciles the active theme
        #    back to a stale marketplace install (the "wrong theme returns" bug).
        #    Deactivate ALL installs for the store, then re-activate the target
        #    theme's install if the theme exists in the marketplace catalog.
        mkt_theme_id = (
            await s.execute(
                text("SELECT id FROM marketplace_themes WHERE slug=:slug"),
                {"slug": slug},
            )
        ).scalar()
        await s.execute(
            text(
                "UPDATE marketplace_theme_installations SET is_active=false "
                "WHERE store_id=:sid"
            ),
            {"sid": store_id},
        )
        if mkt_theme_id:
            await s.execute(
                text(
                    "UPDATE marketplace_theme_installations SET is_active=true, "
                    "uninstalled_at=NULL WHERE store_id=:sid AND marketplace_theme_id=:mkt"
                ),
                {"sid": store_id, "mkt": mkt_theme_id},
            )

        await s.commit()
        print(f"ACTIVATED {slug} on {store_sub}")
        print(f"  theme_id        = {theme_id}")
        print(f"  store_id        = {store_id}")
        print(f"  store_theme     = {store_theme_id}")
        print(f"  theme_version   = {ver_id} ({dev_version})")
        print(f"  bundle_url      = {bundle_url}")
        print(
            f"  marketplace mirror = {'set' if mkt_theme_id else 'cleared (not in catalog)'}"
        )
        print(f"  sections synced = {len(section_schemas)}")


async def restore(store_sub: str, store_theme_id: str) -> None:
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as s:
        store_id = (
            await s.execute(
                text("SELECT id FROM stores WHERE subdomain=:sub"), {"sub": store_sub}
            )
        ).scalar()
        if not store_id:
            raise SystemExit(f"store not found: {store_sub}")
        await s.execute(
            text(
                "UPDATE store_themes SET is_active=(id=:id), updated_at=:now "
                "WHERE store_id=:sid"
            ),
            {"id": store_theme_id, "now": now, "sid": store_id},
        )
        row = (
            await s.execute(
                text(
                    "SELECT t.slug FROM store_themes st JOIN themes t ON t.id=st.theme_id "
                    "WHERE st.id=:id"
                ),
                {"id": store_theme_id},
            )
        ).first()
        # Keep the marketplace mirror consistent with the restored theme.
        if row:
            mkt = (
                await s.execute(
                    text("SELECT id FROM marketplace_themes WHERE slug=:slug"),
                    {"slug": row.slug},
                )
            ).scalar()
            await s.execute(
                text(
                    "UPDATE marketplace_theme_installations SET is_active=false "
                    "WHERE store_id=:sid"
                ),
                {"sid": store_id},
            )
            if mkt:
                await s.execute(
                    text(
                        "UPDATE marketplace_theme_installations SET is_active=true, "
                        "uninstalled_at=NULL WHERE store_id=:sid AND marketplace_theme_id=:mkt"
                    ),
                    {"sid": store_id, "mkt": mkt},
                )
        await s.commit()
        print(
            f"RESTORED active store_theme {store_theme_id} "
            f"({row.slug if row else '?'}) on {store_sub}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", help="theme slug (themes.slug), e.g. luxury-minimal-v3")
    ap.add_argument("--dist", help="path to the theme's built dist/ directory")
    ap.add_argument("--store", default="testlocal", help="store subdomain")
    ap.add_argument("--port", type=int, default=5173, help="local port serving dist/")
    ap.add_argument(
        "--restore", action="store_true", help="reactivate a store_theme row"
    )
    ap.add_argument(
        "--store-theme-id", help="store_themes.id to reactivate (with --restore)"
    )
    ap.add_argument(
        "--force", action="store_true", help="allow running against staging/production"
    )
    a = ap.parse_args()
    _guard(a.force)
    if a.restore:
        if not a.store_theme_id:
            ap.error("--restore requires --store-theme-id")
        asyncio.run(restore(a.store, a.store_theme_id))
    else:
        if not a.slug or not a.dist:
            ap.error("activation requires --slug and --dist")
        asyncio.run(activate(a.slug, a.dist, a.store, a.port))


if __name__ == "__main__":
    main()
