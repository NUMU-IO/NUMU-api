"""TikTok Catalog product feed — sibling of ``meta_feed``.

Public per-store product feed at ``/api/v1/storefront/tiktok-feed/{subdomain}.xml``.
TikTok's Catalog Manager ingests a Google Merchant Center RSS feed (the exact
format Meta accepts), so this reuses the Meta feed's XML builder — only the
entry ``id`` differs: TikTok dynamic ads match a conversion to a catalog row
by ``content_id``, and our TikTok Pixel/Events API fire ``content_id =
str(product.id)``, so the feed ``g:id`` must be the product UUID (not the
Meta-catalog override the Meta feed uses).

**v1 scope:** on-demand per request, excludes drafts/archived/out-of-stock
(unless untracked), 404 for unknown subdomains, EGP default, no auth (TikTok's
crawler hits the URL anonymously). Marketing-API catalog push is a follow-up.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status

from src.api.dependencies import get_store_repository
from src.api.v1.routes.storefront.meta_feed import _build_feed_xml
from src.core.entities.product import ProductStatus
from src.core.entities.store import StoreStatus
from src.infrastructure.repositories import StoreRepository

router = APIRouter()


def _product_to_tiktok_feed_item(
    product: dict, *, store_subdomain: str, currency: str
) -> dict | None:
    """Map one ``products`` row → TikTok feed entry (same shape as Meta's).

    Delta vs the Meta mapper: ``id`` is the product UUID (optionally a
    ``tiktok_catalog_id`` override) so it matches the ``content_id`` the
    TikTok Pixel/Events API send.
    """
    if product.get("status") and str(product["status"]).lower() == "draft":
        return None

    quantity = int(product.get("quantity") or 0)
    track_inventory = bool(product.get("track_inventory", True))
    availability = (
        "in stock" if (not track_inventory or quantity > 0) else "out of stock"
    )

    price_cents = int(product.get("price_amount") or 0)
    price_decimal = f"{price_cents / 100:.2f}"

    images = product.get("images") or []
    image_link = images[0] if images else None

    base_url = f"https://{store_subdomain}.numu.store"
    product_url = f"{base_url}/product/{product['id']}"

    feed_id = product.get("tiktok_catalog_id") or str(product["id"])
    attrs = (
        product.get("attributes") if isinstance(product.get("attributes"), dict) else {}
    )

    return {
        "id": feed_id,
        "item_group_id": str(product["id"]),
        "title": str(product.get("name") or "")[:150],
        "description": str(
            product.get("description") or product.get("short_description") or ""
        )[:5000],
        "link": product_url,
        "image_link": image_link,
        "availability": availability,
        "condition": "new",
        "price": f"{price_decimal} {currency.upper()}",
        "brand": (attrs or {}).get("brand"),
        "product_type": (attrs or {}).get("product_type"),
        "sku": product.get("sku"),
    }


@router.get(
    "/tiktok-feed/{subdomain}.xml",
    summary="Public TikTok Catalog product feed",
    operation_id="tiktok_feed_xml",
    responses={
        200: {
            "content": {"application/xml": {}},
            "description": "RSS 2.0 + g: namespace feed TikTok's Catalog ingests",
        },
        404: {"description": "Store not found or not published"},
    },
)
async def tiktok_catalog_feed(
    subdomain: Annotated[str, Path(description="Store subdomain")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Serve the product catalog as a TikTok-compatible RSS XML feed."""
    normalized = subdomain.lower()
    store = await store_repo.get_by_subdomain(normalized)
    if not store or store.status == StoreStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store not found",
        )

    from sqlalchemy import text

    session = store_repo._session  # noqa: SLF001 — internal but stable
    await session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(store.tenant_id)},
    )
    rows = await session.execute(
        text(
            """
            SELECT id::text AS id, name, description, short_description, sku,
                   price_amount, status::text AS status, quantity,
                   images, attributes,
                   COALESCE((attributes->>'track_inventory')::boolean, true) AS track_inventory
            FROM public.products
            WHERE store_id = :sid
              AND status = :active_status
            ORDER BY created_at DESC
            LIMIT 5000
            """
        ),
        {"sid": str(store.id), "active_status": ProductStatus.ACTIVE.value},
    )
    products_raw = [dict(r._mapping) for r in rows.fetchall()]

    currency = (getattr(store, "default_currency", None) or "EGP").upper()
    items: list[dict] = []
    for p in products_raw:
        entry = _product_to_tiktok_feed_item(
            p, store_subdomain=normalized, currency=currency
        )
        if entry is not None:
            items.append(entry)

    xml = _build_feed_xml(
        store_name=store.name,
        store_url=f"https://{normalized}.numu.store",
        items=items,
    )
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )
