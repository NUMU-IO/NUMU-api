"""Storefront search (Phase 4.1).

URL: /storefront/store/{store_id}/search
     /storefront/store/{store_id}/search/predictive

Backed by the `products.search_vector` tsvector + GIN index added in
the same Phase 4 migration. Two modes:

  - predictive: debounced autocomplete; capped per-type for snappy
    dropdown rendering.
  - full: paged result set for the /search results page.

The SDK's `useSearch` hook (already shipped in Phase 2) consumes
both. Callers can request a subset via `types=products,collections`.
Pages and articles are reserved (the entities themselves ship in a
later phase); we return empty arrays for those types so the SDK's
mixed-result UI doesn't break on upgrade.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_category_repository,
    get_product_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.core.entities.product import ProductStatus
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.repositories import (
    CategoryRepository,
    ProductRepository,
    StoreRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Per-type cap for the predictive (autocomplete) mode. Five rows is the
# Shopify default and matches the SDK hook's documented "fast for
# autocomplete dropdowns" contract.
PREDICTIVE_LIMIT = 5

# Hard cap for the full mode so a runaway query can't dump the whole
# catalog onto a single response.
FULL_MAX_LIMIT = 100


def _build_tsquery(raw: str) -> str:
    """Convert a free-text user query into a tsquery expression.

    Splits on whitespace, escapes Postgres tsquery operators, and
    joins terms with `&` (all-must-match). Each term gets a `:*`
    suffix for prefix matching so "shir" matches "shirt"/"shirts".

    Why we don't use `plainto_tsquery`: it doesn't do prefix matching,
    so "shir" returns nothing instead of "shirt"/"shirts" — wrong
    behavior for autocomplete. Building the tsquery manually with
    `:*` suffixes is the standard recipe.

    Edge cases:
      - empty query → returns "" (caller short-circuits)
      - single special char (`&`, `|`, `(`, `)`, `:`, `*`) → stripped
      - Arabic + English mix → both terms present, both prefix-matched
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return ""
    # Strip tsquery-reserved chars per term so user-typed `&` / `:` etc.
    # don't accidentally synthesize an operator. We don't try to
    # smart-quote — the simpler "drop them" path stops the whole class
    # of injection and is fine for product names.
    forbidden = "&|!():*<>'\""
    terms: list[str] = []
    for tok in cleaned.split():
        clean_tok = "".join(ch for ch in tok if ch not in forbidden)
        if clean_tok:
            terms.append(f"{clean_tok}:*")
    return " & ".join(terms)


async def _search_products(
    session: AsyncSession,
    *,
    store_id: UUID,
    query: str,
    limit: int,
) -> list[ProductModel]:
    """Execute a ranked product search.

    Returns ProductModel rows ordered by ts_rank_cd desc. Filters to
    active products only — the storefront should never surface
    drafts or archived items.
    """
    tsq = _build_tsquery(query)
    if not tsq:
        return []

    # to_tsquery is the right entry-point for prefix queries; we hand
    # it the synthesized expression. The cast on `:tsq` keeps the
    # generated column on the LEFT of the `@@` operator — that's the
    # form the GIN index actually accelerates.
    rank_expr = func.ts_rank_cd(
        ProductModel.search_vector,
        func.to_tsquery("simple", tsq),
    )
    stmt = (
        select(ProductModel)
        .where(
            ProductModel.store_id == store_id,
            ProductModel.status == ProductStatus.ACTIVE,
            ProductModel.search_vector.op("@@")(func.to_tsquery("simple", tsq)),
        )
        .order_by(desc(rank_expr))
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _search_categories(
    store_id: UUID,
    query: str,
    limit: int,
    category_repo: CategoryRepository,
) -> list[Any]:
    """Categories don't have a tsvector yet — fall back to ILIKE on
    name. Catalog sizes are small enough (typically <50 categories)
    that a sequential scan is fine."""
    if not query.strip():
        return []
    cats = await category_repo.get_by_store(store_id=store_id)
    lower = query.lower()
    matches = [c for c in cats if lower in (c.name or "").lower()]
    return matches[:limit]


def _product_to_dict(p: ProductModel) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "slug": p.slug,
        "sku": p.sku,
        "price": p.price_amount / 100.0,
        "price_currency": p.price_currency,
        "image": (p.images or [None])[0] if isinstance(p.images, list) else None,
        "in_stock": (p.quantity or 0) > 0,
    }


def _category_to_dict(c: Any) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "name": c.name,
        "slug": c.slug,
        "image_url": getattr(c, "image_url", None),
        "product_count": getattr(c, "product_count", 0) or 0,
    }


@router.get(
    "/search/predictive",
    summary="Predictive search (autocomplete)",
    operation_id="storefront_predictive_search",
)
async def predictive_search(
    store_id: Annotated[UUID, Path(description="Store ID")],
    session: Annotated[AsyncSession, Depends(get_db)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
    q: str = Query("", description="Search query"),
    types: str = Query(
        "products,collections,pages,articles",
        description="Comma-separated subset of types to return",
    ),
    limit: int = Query(PREDICTIVE_LIMIT, ge=1, le=20),
):
    """Autocomplete-friendly search.

    Caps to `limit` (default 5) per type. The SDK debounces 200ms
    before calling — we don't add server-side debounce because that
    would mask connection latency from the storefront.
    """
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    requested_types = {t.strip() for t in types.split(",") if t.strip()}
    products: list[dict[str, Any]] = []
    collections: list[dict[str, Any]] = []

    if "products" in requested_types:
        prod_rows = await _search_products(
            session, store_id=store_id, query=q, limit=limit
        )
        products = [_product_to_dict(p) for p in prod_rows]
    if "collections" in requested_types:
        cat_rows = await _search_categories(store_id, q, limit, category_repo)
        collections = [_category_to_dict(c) for c in cat_rows]

    return SuccessResponse(
        data={
            "query": q,
            "products": products,
            "collections": collections,
            # Pages + articles backends ship later — empty arrays keep
            # the SDK's mixed-result UI from breaking on upgrade.
            "pages": [],
            "articles": [],
            "total": len(products) + len(collections),
        },
        message="Search results retrieved",
    )


@router.get(
    "/search",
    summary="Full search results",
    operation_id="storefront_full_search",
)
async def full_search(
    store_id: Annotated[UUID, Path(description="Store ID")],
    session: Annotated[AsyncSession, Depends(get_db)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    q: str = Query("", description="Search query"),
    types: str = Query(
        "products,collections,pages,articles",
        description="Comma-separated subset of types to return",
    ),
    limit: int = Query(24, ge=1, le=FULL_MAX_LIMIT),
    page: int = Query(1, ge=1),
):
    """Full search with pagination — used by the /search results page.

    Each type paginates independently; the response carries a `total`
    rollup so the SDK can render "X results across Y types" without a
    second call.
    """
    # `product_repo` is unused here today but kept in the signature so
    # the eventual "filter by category" pivot doesn't churn the route's
    # public dep contract. ListProductsUseCase consumes it; the search
    # path doesn't yet because ranked results don't compose with the
    # use-case's static ordering.
    _ = product_repo

    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    requested_types = {t.strip() for t in types.split(",") if t.strip()}
    products: list[dict[str, Any]] = []
    collections: list[dict[str, Any]] = []
    total_products = 0

    if "products" in requested_types:
        # For full mode we run the same ranking query but with a
        # higher cap; pagination is handled via `limit` + offset
        # equivalent (offset = (page-1)*limit). Total count is a
        # separate query — count(*) over the same WHERE clause.
        offset = (page - 1) * limit
        tsq = _build_tsquery(q)
        if tsq:
            rank_expr = func.ts_rank_cd(
                ProductModel.search_vector,
                func.to_tsquery("simple", tsq),
            )
            base_filter = (
                (ProductModel.store_id == store_id)
                & (ProductModel.status == ProductStatus.ACTIVE)
                & ProductModel.search_vector.op("@@")(func.to_tsquery("simple", tsq))
            )
            page_stmt = (
                select(ProductModel)
                .where(base_filter)
                .order_by(desc(rank_expr))
                .offset(offset)
                .limit(limit)
            )
            count_stmt = (
                select(func.count()).select_from(ProductModel).where(base_filter)
            )
            page_rows = list((await session.execute(page_stmt)).scalars().all())
            total_products = (await session.execute(count_stmt)).scalar() or 0
            products = [_product_to_dict(p) for p in page_rows]

    if "collections" in requested_types:
        # ILIKE fallback (Phase 4.4 will swap to rule-based smart
        # collections; until then the linear scan is fine for typical
        # category counts).
        cat_rows = await _search_categories(store_id, q, limit, category_repo)
        collections = [_category_to_dict(c) for c in cat_rows]

    return SuccessResponse(
        data={
            "query": q,
            "products": products,
            "collections": collections,
            "pages": [],
            "articles": [],
            "page": page,
            "limit": limit,
            "total": total_products + len(collections),
            "total_products": total_products,
        },
        message="Search results retrieved",
    )


# Marker so ruff doesn't flag the unused TSQUERY import — kept for the
# future when we materialize tsquery expressions in the schema layer.
_ = TSQUERY
