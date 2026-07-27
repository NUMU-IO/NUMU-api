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
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx

logger = logging.getLogger(__name__)


@dataclass
class RevalidationSummary:
    """Structured outcome of a single storefront revalidation call.

    Surfaced in the publish API response so the merchant hub can show an
    honest state ("Live" vs "Saved, storefront refresh delayed") instead of
    a misleading "live" success when the bust silently no-op'd (missing
    secret, storefront down, tag mismatch).
    """

    requested: bool = False  # we actually attempted the POST (secret + tags present)
    succeeded: bool = False  # storefront returned 200
    tags_requested: list[str] = field(default_factory=list)
    tags_revalidated: list[str] = field(default_factory=list)
    duration_ms: int = 0
    status_code: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "succeeded": self.succeeded,
            "tags_requested": self.tags_requested,
            "tags_revalidated": self.tags_revalidated,
            "duration_ms": self.duration_ms,
            "status_code": self.status_code,
            "error": self.error,
        }


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


def theme_cache_tag(store_id: str) -> str:
    """Cache tag for a store's theme/customization-dependent pages.

    MUST stay byte-identical to the tag the storefront attaches to its theme
    fetch — ``theme-${storeId}`` in ``numu-storefront/src/lib/api-client.ts``.
    A prior mismatch (``theme:{id}`` colon here vs ``theme-{id}`` hyphen
    there) made ``revalidateTag`` a no-op, so Publish never refreshed the
    live store until the ISR window lapsed.
    """
    return f"theme-{store_id}"


def store_cache_tag(subdomain: str) -> str:
    """Cache tag for a store's base data.

    Matches the storefront's ``store-${subdomain}`` tag in ``api-client.ts``
    (hyphen, not colon).
    """
    return f"store-{subdomain}"


def store_cache_tags(
    subdomain: str | None, custom_domain: str | None = None
) -> list[str]:
    """All cache tags the storefront uses for a store's base payload.

    The storefront tags its store fetch ``store-${subdomain}`` AND, for
    custom-domain stores, ``store-${host}`` (``api-client.ts`` lines 82/87).
    A publish that only busts ``theme-{id}`` leaves merchant-editable fields
    that ride the base payload (store name, logo, SEO, social, ``theme_settings``)
    behind the un-busted 300s ``store-`` cache entry, so they wait out the full
    ISR window. Bust both.
    """
    tags: list[str] = []
    if subdomain:
        tags.append(store_cache_tag(subdomain))
    if custom_domain:
        tags.append(f"store-{custom_domain}")
    return tags


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
        # WARNING, not DEBUG: a missing secret silently degrades every publish
        # to "wait out the ISR window" with no operator-visible signal, while
        # the publish endpoint still returns 200. Make it loud.
        logger.warning(
            "REVALIDATION_SECRET not set — storefront cache will NOT be busted "
            "on publish; merchant edits wait out the ISR window. subdomain=%s",
            subdomain,
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


async def revalidate_store_traced(
    subdomain: str,
    paths: list[str] | None = None,
    tags: list[str] | None = None,
    scope: Literal["layout", "page"] | None = None,
) -> RevalidationSummary:
    """Like :func:`revalidate_store` but returns a :class:`RevalidationSummary`.

    Use on the publish path where the caller wants to report the freshness
    outcome back to the merchant UI rather than treating the bust as
    fire-and-forget.
    """
    summary = RevalidationSummary(tags_requested=list(tags or []))
    if not REVALIDATION_SECRET:
        logger.warning(
            "REVALIDATION_SECRET not set — storefront cache will NOT be busted "
            "on publish; merchant edits wait out the ISR window. subdomain=%s",
            subdomain,
        )
        summary.error = "revalidation_secret_not_configured"
        return summary

    if not (paths or tags):
        summary.error = "no_paths_or_tags"
        return summary

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

    summary.requested = True
    started = time.monotonic()
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
        summary.duration_ms = int((time.monotonic() - started) * 1000)
        summary.status_code = response.status_code
        if response.status_code != 200:
            summary.error = f"http_{response.status_code}: {response.text[:200]}"
            logger.warning(
                "Revalidation failed for %s: %s %s",
                subdomain,
                response.status_code,
                response.text[:200],
            )
            return summary
        # Parse the storefront's structured response to confirm WHICH tags it
        # actually expired. Tolerant of both the new (`tagsRevalidated`) and
        # legacy (`revalidated.tags`) shapes.
        try:
            body = response.json()
        except Exception:
            body = {}
        revalidated = body.get("revalidated") if isinstance(body, dict) else None
        if isinstance(body, dict) and isinstance(body.get("tagsRevalidated"), list):
            summary.tags_revalidated = [str(t) for t in body["tagsRevalidated"]]
        elif isinstance(revalidated, dict) and isinstance(
            revalidated.get("tags"), list
        ):
            summary.tags_revalidated = [str(t) for t in revalidated["tags"]]
        else:
            # Storefront didn't echo tags — assume it honored the request.
            summary.tags_revalidated = list(tags or [])
        summary.succeeded = True
        logger.info(
            "Revalidation succeeded for %s in %dms",
            subdomain,
            summary.duration_ms,
            extra={"tags": summary.tags_revalidated},
        )
        return summary
    except httpx.HTTPError as e:
        summary.duration_ms = int((time.monotonic() - started) * 1000)
        summary.error = f"http_error: {e}"
        logger.warning("Revalidation HTTP error for %s: %s", subdomain, e)
        return summary


async def revalidate_on_customization_publish_traced(
    subdomain: str, store_id: str, custom_domain: str | None = None
) -> RevalidationSummary:
    """Traced variant of :func:`revalidate_on_customization_publish`.

    Returns the structured outcome so the publish endpoint can include a
    revalidation summary in its response.
    """
    return await revalidate_store_traced(
        subdomain=subdomain,
        tags=[theme_cache_tag(store_id), *store_cache_tags(subdomain, custom_domain)],
        scope="layout",
    )


# ── IndexNow (proactive search-engine notification) ──────────────────────────


def _storefront_url(subdomain: str, path: str) -> str:
    """Absolute URL of a storefront endpoint for one store."""
    if "{subdomain}" in STOREFRONT_BASE_URL:
        base = STOREFRONT_BASE_URL.format(subdomain=subdomain)
    else:
        base = STOREFRONT_BASE_URL
    return base.rstrip("/") + path


async def ping_indexnow(subdomain: str, paths: list[str] | None = None) -> bool:
    """Best-effort IndexNow submission for a store's changed URLs.

    Cache busting only makes a change visible to someone who *visits*; nothing
    tells a search engine the page moved. IndexNow is a single POST that Bing,
    DuckDuckGo and Yandex act on within minutes — and Bing's index is what
    feeds ChatGPT Search — so this is the shortest path from "merchant hit
    Publish" to "an answer engine can cite the new page".

    We post store-relative paths to the storefront's ``/api/indexnow``, which
    carries the same ``x-revalidation-secret`` contract as ``/api/revalidate``.
    The storefront resolves each path against the store's canonical origin and
    rejects anything that isn't on it, so this side never has to know whether
    the merchant is on a subdomain or a verified custom domain — and a bug here
    can't submit URLs for a host we don't own.

    ⚠️ Pass CANONICAL paths. The PDP path this module posts for cache busting is
    the legacy singular ``/product/{slug}``; the URL that is actually indexed
    (what ``sitemap.xml`` and ``rel=canonical`` emit) is the plural
    ``/products/{slug}``. Announcing the other form asks an engine to index a
    duplicate of a page it already has.

    Returns True only when the storefront reported a submission. Never raises:
    a marketing ping that fails is a missed opportunity, not a reason for a
    merchant's publish to error.
    """
    if not REVALIDATION_SECRET:
        # Quiet on purpose — revalidate_store already logs this loudly on the
        # same publish, and two warnings for one missing var is just noise.
        return False

    urls = [p for p in (paths or []) if p and p.startswith("/")]
    if not urls:
        return False

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                _storefront_url(subdomain, "/api/indexnow"),
                headers={
                    "x-revalidation-secret": REVALIDATION_SECRET,
                    "Content-Type": "application/json",
                },
                json={"urls": urls},
            )
            if response.status_code != 200:
                logger.warning(
                    "IndexNow ping failed for %s: %s %s",
                    subdomain,
                    response.status_code,
                    response.text[:200],
                )
                return False
            body = response.json()
            submitted = bool(body.get("submitted")) if isinstance(body, dict) else False
            if not submitted:
                # The storefront answers 200 with a reason for every soft skip
                # (key not configured, store blocks indexing, dev host). Log it
                # at INFO — these are expected states, not failures.
                logger.info(
                    "IndexNow ping skipped for %s: %s",
                    subdomain,
                    body.get("reason") if isinstance(body, dict) else "unknown",
                )
            return submitted
    except Exception as e:  # noqa: BLE001 — must never surface to the merchant
        # Broader than the httpx.HTTPError the revalidation calls catch: this
        # one is pure marketing, so even a JSON-decode or URL-construction bug
        # must not turn a successful publish into a 500.
        logger.warning("IndexNow ping error for %s: %s", subdomain, e)
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
    # Only the PDP, and only in its CANONICAL plural form — the `/product/…`
    # and `/products` entries above exist to bust caches, not to be indexed,
    # and re-announcing the home page on every price edit is exactly the
    # unchanged-URL spam IndexNow asks callers not to send.
    await ping_indexnow(subdomain, [f"/products/{product_slug}"])


async def revalidate_on_theme_activate(
    subdomain: str, store_id: str, custom_domain: str | None = None
) -> None:
    """Call when a store activates a new theme.

    Busts the theme tag plus the base store payload tags for BOTH the
    subdomain and (when present) the custom domain, so custom-domain stores
    don't keep rendering the old theme until the ISR window lapses.
    """
    await revalidate_store(
        subdomain=subdomain,
        paths=["/"],
        tags=[theme_cache_tag(store_id), *store_cache_tags(subdomain, custom_domain)],
        scope="layout",
    )


async def revalidate_on_customization_publish(
    subdomain: str, store_id: str, custom_domain: str | None = None
) -> None:
    """Call when a merchant publishes draft customization.

    Busts both the ``theme-{id}`` tag (the V3 customization fetch) AND the
    ``store-{subdomain}`` / ``store-{custom_domain}`` tags, because several
    merchant-editable fields (name, logo, SEO, social, ``theme_settings``) are
    served from the base store payload behind a 300s ISR window. Dropped the
    old ``paths=["/"]`` arg — the live storefront's tenant routes are
    ``/[domain]/…`` (host→path rewrite), not ``/`` (that's the apex marketing
    page), so revalidating ``/`` did nothing for stores; tags carry the load.
    """
    await revalidate_store(
        subdomain=subdomain,
        tags=[theme_cache_tag(store_id), *store_cache_tags(subdomain, custom_domain)],
        scope="layout",
    )


async def revalidate_on_menu_change(subdomain: str, store_id: str) -> None:
    """Call when a store's navigation menu changes.

    Busts the storefront's cached menu fetch (tagged ``menus-{store_id}``)
    plus the theme/layout so header/footer nav refresh without waiting out
    the ISR window.
    """
    await revalidate_store(
        subdomain=subdomain,
        paths=["/"],
        tags=[f"menus-{store_id}", theme_cache_tag(store_id)],
        scope="layout",
    )


async def revalidate_on_page_change(subdomain: str, store_id: str, handle: str) -> None:
    """Call when a merchant content page is created/updated/deleted.

    Busts the storefront's cached page fetch (tagged ``pages-{store_id}``)
    plus the specific page path so ``/pages/<handle>`` refreshes without
    waiting out the ISR window.
    """
    await revalidate_store(
        subdomain=subdomain,
        paths=[f"/pages/{handle}"],
        tags=[f"pages-{store_id}", theme_cache_tag(store_id)],
    )
    await ping_indexnow(subdomain, [f"/pages/{handle}"])


async def revalidate_on_blog_change(
    subdomain: str,
    store_id: str,
    blog_handle: str,
    article_handle: str | None = None,
) -> None:
    """Call when a blog or article is created/updated/deleted/published.

    Busts the storefront's cached blog fetches (tagged ``blogs-{store_id}``)
    plus the specific paths so scheduled publishes appear without waiting
    out the ISR window.
    """
    paths = ["/blogs", f"/blogs/{blog_handle}"]
    if article_handle:
        paths.append(f"/blogs/{blog_handle}/{article_handle}")
    await revalidate_store(
        subdomain=subdomain,
        paths=paths,
        tags=[f"blogs-{store_id}"],
    )
    # Here the cache-bust paths ARE the canonical URLs, and all three genuinely
    # changed (a new article changes the index and its blog listing too), so
    # the same list is what we announce.
    await ping_indexnow(subdomain, paths)


async def revalidate_on_metafield_change(
    subdomain: str,
    store_id: str,
    product_slugs: list[str] | None = None,
) -> None:
    """Call when a metafield definition's visibility changes, a definition is
    deleted, or a value is set/unset.

    Metafields ride on the *product detail* payload, which the storefront tags
    both per-product (``product:{store_id}:{slug}``) and store-wide
    (``products:{store_id}``) — so the store-wide tag alone reaches every PDP.
    We post the per-product tags too when we know which owners changed, because
    the store-wide tag is the newer of the two and an older storefront build may
    not carry it yet.

    This matters more than an ordinary cache miss: flipping a field to private
    and having it keep rendering is a data leak with a stale-cache excuse. The
    API-side Redis sweep is not enough — the shopper-facing surface is a
    separate cache in a separate process.
    """
    tags: list[str] = [
        f"products:{store_id}",
        f"categories:{store_id}",
    ]
    for slug in product_slugs or []:
        tags.append(f"product:{store_id}:{slug}")
    await revalidate_store(subdomain=subdomain, paths=["/products"], tags=tags)


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
