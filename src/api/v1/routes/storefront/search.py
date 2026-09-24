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

`authors` and `series` groups serve bookstore autocomplete. They are
NOT counted in `total`: the SDK hook never sends `types`, so it gets
them by default, drops them, and would render a count of rows it
never shows. `total` stays products + collections.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import and_, desc, exists, func, or_, select
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
from src.infrastructure.database.models.tenant.series import (
    SeriesModel,
    SeriesProductModel,
)
from src.infrastructure.database.models.tenant.variant import VariantModel
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

DEFAULT_TYPES = "products,collections,pages,articles,authors,series"


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


def _normalize_isbn(raw: str) -> str | None:
    """Return the query as a bare ISBN-10/13 (hyphens dropped, X upper-cased),
    or None when it isn't shaped like one."""
    cleaned = (raw or "").strip()
    if not re.fullmatch(r"[\dXx-]+", cleaned):
        return None
    bare = cleaned.replace("-", "").upper()
    return bare if re.fullmatch(r"\d{9}[\dX]|\d{13}", bare) else None


def _bare(column: Any) -> Any:
    return func.upper(func.replace(column, "-", ""))


def _product_search(store_id: UUID, query: str) -> tuple[Any, list[Any]] | None:
    """WHERE clause and ORDER BY for a storefront product search.

    A product matches on the tsvector, its `attributes.author`, an exact
    ISBN (`attributes.isbn`, product sku or a variant sku), or the name of
    an active series it belongs to. Tsvector hits come first by rank; the
    rest follow by name. The OR means the GIN index can't answer alone, so
    Postgres scans the store's active products — fine at catalogue sizes
    of a few thousand, needs a trigram index beyond that.
    """
    q = (query or "").strip()
    if not q:
        return None
    matches: list[Any] = [
        ProductModel.attributes["author"].as_string().icontains(q, autoescape=True),
        ProductModel.id.in_(
            select(SeriesProductModel.product_id)
            .join(SeriesModel, SeriesModel.id == SeriesProductModel.series_id)
            .where(
                SeriesModel.store_id == store_id,
                SeriesModel.status == "active",
                SeriesModel.name.icontains(q, autoescape=True),
            )
        ),
    ]
    order: list[Any] = []
    tsq = _build_tsquery(q)
    if tsq:
        ts_query = func.to_tsquery("simple", tsq)
        ts_match = ProductModel.search_vector.op("@@")(ts_query)
        matches.append(ts_match)
        order = [
            desc(ts_match),
            desc(func.ts_rank_cd(ProductModel.search_vector, ts_query)),
        ]
    isbn = _normalize_isbn(q)
    if isbn:
        matches += [
            _bare(ProductModel.attributes["isbn"].as_string()) == isbn,
            _bare(ProductModel.sku) == isbn,
            exists().where(
                VariantModel.product_id == ProductModel.id,
                _bare(VariantModel.sku) == isbn,
            ),
        ]
    where = and_(
        ProductModel.store_id == store_id,
        ProductModel.status == ProductStatus.ACTIVE,
        or_(*matches),
    )
    return where, [*order, ProductModel.name, ProductModel.id]


async def _search_products(
    session: AsyncSession,
    *,
    store_id: UUID,
    query: str,
    limit: int,
    offset: int = 0,
) -> list[ProductModel]:
    search = _product_search(store_id, query)
    if search is None:
        return []
    where, order = search
    stmt = (
        select(ProductModel).where(where).order_by(*order).offset(offset).limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _count_products(session: AsyncSession, *, store_id: UUID, query: str) -> int:
    search = _product_search(store_id, query)
    if search is None:
        return 0
    stmt = select(func.count()).select_from(ProductModel).where(search[0])
    return (await session.execute(stmt)).scalar() or 0


async def _search_authors(
    session: AsyncSession, *, store_id: UUID, query: str, limit: int
) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    author = func.btrim(ProductModel.attributes["author"].as_string())
    names = (
        select(author.label("name"))
        .where(
            ProductModel.store_id == store_id,
            ProductModel.status == ProductStatus.ACTIVE,
            author.icontains(q, autoescape=True),
        )
        .subquery()
    )
    key = func.lower(names.c.name)
    count = func.count()
    stmt = (
        select(func.mode().within_group(names.c.name), count)
        .group_by(key)
        .order_by(desc(count), key)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [{"name": name, "product_count": n} for name, n in rows]


async def _search_series(
    session: AsyncSession, *, store_id: UUID, query: str, limit: int
) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    count = func.count(ProductModel.id)
    stmt = (
        select(SeriesModel.id, SeriesModel.name, SeriesModel.slug, count)
        .outerjoin(SeriesProductModel, SeriesProductModel.series_id == SeriesModel.id)
        .outerjoin(
            ProductModel,
            and_(
                ProductModel.id == SeriesProductModel.product_id,
                ProductModel.status == ProductStatus.ACTIVE,
            ),
        )
        .where(
            SeriesModel.store_id == store_id,
            SeriesModel.status == "active",
            SeriesModel.name.icontains(q, autoescape=True),
        )
        .group_by(SeriesModel.id)
        .order_by(desc(count), SeriesModel.name)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {"id": str(sid), "name": name, "slug": slug, "product_count": n}
        for sid, name, slug, n in rows
    ]


async def _search_categories(
    store_id: UUID,
    query: str,
    limit: int,
    category_repo: CategoryRepository,
) -> list[Any]:
    """Categories don't have a tsvector yet, so match the name in SQL."""
    if not query.strip():
        return []
    return await category_repo.search_by_name(store_id, query, limit)


def _product_to_dict(p: ProductModel) -> dict[str, Any]:
    # A search result is rendered by the same product card as a collection
    # grid, so it carries what that card reads: the full gallery, the
    # compare-at price, tags, and the free-form attributes where an imported
    # catalogue keeps its author. With only name/price/one image, every
    # result lost its author line and its struck-through price.
    images = p.images if isinstance(p.images, list) else []
    return {
        "id": str(p.id),
        "name": p.name,
        "slug": p.slug,
        "sku": p.sku,
        "price": p.price_amount / 100.0,
        "compare_at_price": p.compare_at_price / 100.0 if p.compare_at_price else None,
        "price_currency": p.price_currency,
        "image": images[0] if images else None,
        "images": images,
        "tags": p.tags or [],
        "attributes": p.attributes or {},
        "in_stock": (p.quantity or 0) > 0,
        "is_low_stock": 0 < (p.quantity or 0) <= p.low_stock_threshold,
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
        DEFAULT_TYPES,
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
    authors: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []

    if "products" in requested_types:
        prod_rows = await _search_products(
            session, store_id=store_id, query=q, limit=limit
        )
        products = [_product_to_dict(p) for p in prod_rows]
    if "collections" in requested_types:
        cat_rows = await _search_categories(store_id, q, limit, category_repo)
        collections = [_category_to_dict(c) for c in cat_rows]
    if "authors" in requested_types:
        authors = await _search_authors(
            session, store_id=store_id, query=q, limit=limit
        )
    if "series" in requested_types:
        series = await _search_series(session, store_id=store_id, query=q, limit=limit)

    return SuccessResponse(
        data={
            "query": q,
            "products": products,
            "collections": collections,
            # Pages + articles backends ship later — empty arrays keep
            # the SDK's mixed-result UI from breaking on upgrade.
            "pages": [],
            "articles": [],
            "authors": authors,
            "series": series,
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
        DEFAULT_TYPES,
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
    authors: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    total_products = 0

    if "products" in requested_types:
        page_rows = await _search_products(
            session,
            store_id=store_id,
            query=q,
            limit=limit,
            offset=(page - 1) * limit,
        )
        products = [_product_to_dict(p) for p in page_rows]
        total_products = await _count_products(session, store_id=store_id, query=q)

    if "collections" in requested_types:
        # ILIKE fallback (Phase 4.4 will swap to rule-based smart
        # collections; until then the linear scan is fine for typical
        # category counts).
        cat_rows = await _search_categories(store_id, q, limit, category_repo)
        collections = [_category_to_dict(c) for c in cat_rows]
    if "authors" in requested_types:
        authors = await _search_authors(
            session, store_id=store_id, query=q, limit=limit
        )
    if "series" in requested_types:
        series = await _search_series(session, store_id=store_id, query=q, limit=limit)

    return SuccessResponse(
        data={
            "query": q,
            "products": products,
            "collections": collections,
            "pages": [],
            "articles": [],
            "authors": authors,
            "series": series,
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
