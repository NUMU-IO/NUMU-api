"""SEO Phase 4 revalidation regression tests.

`revalidate_on_product_change` had two bugs the SEO Phase 4 commit
fixed:

1. Path was `/products/{slug}` — the storefront route is
   `/product/{id-or-slug}` (singular), so the old path 404'd silently
   and Googlebot kept caching stale metadata.
2. Tag was slug-only. The storefront tags fetches as
   `product:{store_id}:{productId}` where productId is whatever was in
   the visitor URL (slug OR UUID), so visitors who hit a UUID URL
   never saw fresh data.

These tests drive the helper with a patched `revalidate_store` and
assert the recorded payload, mirroring the sync-test-with-private-loop
pattern from `test_tracking_async.py`.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_revalidate_on_product_change_uses_singular_product_path() -> None:
    """Phase 4 fixes the `/products/{slug}` → `/product/{slug}` bug."""
    from src.infrastructure.external_services.nextjs_revalidation import (
        revalidate_on_product_change,
    )

    with patch(
        "src.infrastructure.external_services.nextjs_revalidation.revalidate_store",
        new=AsyncMock(return_value=True),
    ) as mock_rev:
        _run(
            revalidate_on_product_change(
                subdomain="sawsaw",
                store_id="11111111-1111-1111-1111-111111111111",
                product_slug="silver-bracelet",
            )
        )

    kwargs = mock_rev.call_args.kwargs
    assert "/product/silver-bracelet" in kwargs["paths"]
    # Sanity: the broken `/products/<slug>` form must NOT appear.
    assert "/products/silver-bracelet" not in kwargs["paths"]


def test_revalidate_on_product_change_emits_slug_tag() -> None:
    from src.infrastructure.external_services.nextjs_revalidation import (
        revalidate_on_product_change,
    )

    with patch(
        "src.infrastructure.external_services.nextjs_revalidation.revalidate_store",
        new=AsyncMock(return_value=True),
    ) as mock_rev:
        _run(
            revalidate_on_product_change(
                subdomain="sawsaw",
                store_id="store-1",
                product_slug="silver-bracelet",
            )
        )

    tags = mock_rev.call_args.kwargs["tags"]
    assert "products:store-1" in tags
    assert "product:store-1:silver-bracelet" in tags


def test_revalidate_on_product_change_includes_uuid_tag_when_provided() -> None:
    """Visitors arriving via the UUID URL must hit a tag that matches the
    storefront's `product:${storeId}:${productId}` fetch tag."""
    from src.infrastructure.external_services.nextjs_revalidation import (
        revalidate_on_product_change,
    )

    with patch(
        "src.infrastructure.external_services.nextjs_revalidation.revalidate_store",
        new=AsyncMock(return_value=True),
    ) as mock_rev:
        _run(
            revalidate_on_product_change(
                subdomain="sawsaw",
                store_id="store-1",
                product_slug="silver-bracelet",
                product_id="22222222-2222-2222-2222-222222222222",
            )
        )

    paths = mock_rev.call_args.kwargs["paths"]
    tags = mock_rev.call_args.kwargs["tags"]
    assert "/product/22222222-2222-2222-2222-222222222222" in paths
    assert "product:store-1:22222222-2222-2222-2222-222222222222" in tags


def test_revalidate_on_product_change_busts_sitemap_products_tag() -> None:
    """Phase 2 sitemap.ts tags its products fetch as
    `sitemap:products:<store>`; product writes must bust it so the
    sitemap regenerates within the revalidate window."""
    from src.infrastructure.external_services.nextjs_revalidation import (
        revalidate_on_product_change,
    )

    with patch(
        "src.infrastructure.external_services.nextjs_revalidation.revalidate_store",
        new=AsyncMock(return_value=True),
    ) as mock_rev:
        _run(
            revalidate_on_product_change(
                subdomain="sawsaw",
                store_id="store-1",
                product_slug="silver-bracelet",
            )
        )

    assert "sitemap:products:store-1" in mock_rev.call_args.kwargs["tags"]


def test_revalidate_on_category_change_busts_sitemap_categories_tag() -> None:
    from src.infrastructure.external_services.nextjs_revalidation import (
        revalidate_on_category_change,
    )

    with patch(
        "src.infrastructure.external_services.nextjs_revalidation.revalidate_store",
        new=AsyncMock(return_value=True),
    ) as mock_rev:
        _run(revalidate_on_category_change(subdomain="sawsaw", store_id="store-1"))

    tags = mock_rev.call_args.kwargs["tags"]
    assert "categories:store-1" in tags
    assert "sitemap:categories:store-1" in tags


def test_revalidate_sitemaps_helper_only_posts_requested_tags() -> None:
    """Bulk-import path: ask for products only, get only the products tag."""
    from src.infrastructure.external_services.nextjs_revalidation import (
        revalidate_sitemaps,
    )

    with patch(
        "src.infrastructure.external_services.nextjs_revalidation.revalidate_store",
        new=AsyncMock(return_value=True),
    ) as mock_rev:
        _run(
            revalidate_sitemaps(
                subdomain="sawsaw",
                store_id="store-1",
                products=True,
            )
        )

    tags = mock_rev.call_args.kwargs["tags"]
    assert tags == ["sitemap:products:store-1"]
    # Path must include /sitemap.xml so revalidatePath flushes the
    # rendered urlset.
    assert "/sitemap.xml" in mock_rev.call_args.kwargs["paths"]


def test_revalidate_sitemaps_helper_short_circuits_when_nothing_requested() -> None:
    """Defensive: caller passing no flags must not fire a no-op request."""
    from src.infrastructure.external_services.nextjs_revalidation import (
        revalidate_sitemaps,
    )

    with patch(
        "src.infrastructure.external_services.nextjs_revalidation.revalidate_store",
        new=AsyncMock(return_value=True),
    ) as mock_rev:
        _run(revalidate_sitemaps(subdomain="sawsaw", store_id="store-1"))

    assert mock_rev.call_count == 0
