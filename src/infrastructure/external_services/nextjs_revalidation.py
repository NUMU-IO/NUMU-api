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

    # `{subdomain}` placeholder is optional — useful in prod for per-store
    # subdomain routing. In dev (single localhost storefront) the env var
    # has no placeholder and we just append /api/revalidate.
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
    subdomain: str, store_id: str, product_slug: str
) -> None:
    """Call when a product is created/updated/deleted."""
    await revalidate_store(
        subdomain=subdomain,
        paths=[f"/products/{product_slug}", "/products", "/"],
        tags=[
            f"products:{store_id}",
            f"product:{store_id}:{product_slug}",
        ],
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
    """Call when a category is created/updated/deleted."""
    await revalidate_store(
        subdomain=subdomain,
        paths=["/", "/products"],
        tags=[f"categories:{store_id}"],
    )
