"""Next.js on-demand revalidation client.

Whenever data changes in NUMU-api that should invalidate cached pages
in the Next.js storefront, call `revalidate_store(subdomain, paths=..., tags=...)`.

The Next.js storefront exposes a webhook at:
    POST https://{subdomain}.numueg.app/api/revalidate
    Headers: x-revalidation-secret: ${REVALIDATION_SECRET}
    Body: { paths: [...], tags: [...], scope?: "layout" }

Usage:

    from src.infrastructure.external_services.nextjs_revalidation import (
        revalidate_store,
    )

    # After a product update
    await revalidate_store(
        subdomain="mystore",
        tags=[f"products:{store_id}", f"product:{store_id}:{slug}"],
    )

    # After a theme activate
    await revalidate_store(
        subdomain="mystore",
        paths=["/"],
        scope="layout",
    )
"""

from __future__ import annotations

import logging
import os
from typing import Literal

import httpx

logger = logging.getLogger(__name__)


def _read_env(name: str, default: str = "") -> str:
    """Read an env var, falling back to the .env file via pydantic-settings.

    Some workers don't have the OS env populated (uvicorn loads .env into the
    Settings class but not into os.environ). Try OS env first; if missing,
    parse .env directly so this module works in both contexts.
    """
    value = os.getenv(name, "")
    if value:
        return value
    # Fallback: read .env file from project root
    try:
        from pathlib import Path

        env_path = Path(__file__).resolve().parents[3] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() == name:
                    return val.strip().strip('"').strip("'")
    except Exception:
        pass
    return default


REVALIDATION_SECRET = _read_env("REVALIDATION_SECRET")
STOREFRONT_BASE_URL = _read_env(
    "NUMU_STOREFRONT_BASE_URL", "https://{subdomain}.numueg.app"
)


async def revalidate_store(
    subdomain: str,
    paths: list[str] | None = None,
    tags: list[str] | None = None,
    scope: Literal["layout", "page"] | None = None,
) -> bool:
    """Trigger revalidation on the Next.js storefront for a specific store.

    Returns True on success, False on any failure (non-fatal).
    """
    if not REVALIDATION_SECRET:
        logger.debug(
            "REVALIDATION_SECRET not set — skipping revalidation for %s", subdomain
        )
        return False

    if not (paths or tags):
        return False

    if "{subdomain}" in STOREFRONT_BASE_URL:
        base = STOREFRONT_BASE_URL.format(subdomain=subdomain)
    else:
        base = STOREFRONT_BASE_URL
    url = base.rstrip("/") + "/api/revalidate"
    payload: dict[str, object] = {}
    if paths:
        payload["paths"] = paths
    if tags:
        payload["tags"] = tags
    if scope:
        payload["scope"] = scope

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                url,
                headers={
                    "x-revalidation-secret": REVALIDATION_SECRET,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code != 200:
                logger.warning(
                    "Revalidation failed for %s: %s %s",
                    subdomain,
                    response.status_code,
                    response.text[:200],
                )
                return False
            logger.info(
                "Revalidation succeeded for %s",
                subdomain,
                extra={"paths": paths, "tags": tags},
            )
            return True
    except httpx.HTTPError as e:
        logger.warning("Revalidation HTTP error for %s: %s", subdomain, e)
        return False


# ── High-level helpers ────────────────────────────────────────────────────────


async def revalidate_on_product_change(
    subdomain: str,
    store_id: str,
    product_slug: str,
    product_id: str | None = None,
) -> None:
    """Call when a product is created/updated/deleted.

    Posts both slug-keyed and UUID-keyed cache tags + paths so visitors
    arriving via either URL form get fresh metadata. The storefront tags
    fetches as `product:{store_id}:{productId}` where `productId` is whatever
    the visiting URL contained — either could be slug or UUID — so we have
    to bust both. Also busts the sitemap-products tag for Phase 2.

    The storefront PDP route is `/product/{slug-or-uuid}` (singular). The
    previous version of this helper posted `/products/{slug}` which doesn't
    exist on the storefront and silently no-op'd revalidation.
    """
    paths: list[str] = [
        f"/product/{product_slug}",
        "/products",
        "/",
    ]
    tags: list[str] = [
        f"products:{store_id}",
        f"product:{store_id}:{product_slug}",
        f"sitemap:products:{store_id}",
    ]
    if product_id:
        paths.append(f"/product/{product_id}")
        tags.append(f"product:{store_id}:{product_id}")
    await revalidate_store(
        subdomain=subdomain,
        paths=paths,
        tags=tags,
    )


async def revalidate_on_theme_activate(subdomain: str, store_id: str) -> None:
    """Call when a store activates a new theme."""
    await revalidate_store(
        subdomain=subdomain,
        paths=["/"],
        tags=[f"theme:{store_id}", f"store:{subdomain}"],
        scope="layout",
    )


async def revalidate_on_customization_publish(subdomain: str, store_id: str) -> None:
    """Call when a merchant publishes draft customization."""
    await revalidate_store(
        subdomain=subdomain,
        paths=["/"],
        tags=[f"theme:{store_id}"],
        scope="layout",
    )


async def revalidate_on_category_change(subdomain: str, store_id: str) -> None:
    """Call when a category is created/updated/deleted.

    Also busts the categories sitemap tag (Phase 2) so the new/changed
    category surfaces in `<host>/sitemap.xml` within ~60s of save.
    """
    await revalidate_store(
        subdomain=subdomain,
        paths=["/", "/products"],
        tags=[
            f"categories:{store_id}",
            f"sitemap:categories:{store_id}",
        ],
    )


async def revalidate_sitemaps(
    subdomain: str,
    store_id: str,
    *,
    products: bool = False,
    categories: bool = False,
) -> None:
    """Targeted sitemap-only invalidation.

    Useful for bulk-import flows that touch many products/categories
    without going through the per-row update path — call once at the end
    instead of N times during the loop.
    """
    tags: list[str] = []
    if products:
        tags.append(f"sitemap:products:{store_id}")
    if categories:
        tags.append(f"sitemap:categories:{store_id}")
    if not tags:
        return
    await revalidate_store(subdomain=subdomain, paths=["/sitemap.xml"], tags=tags)
