"""Wave 3 Phase 16 — Meta Commerce Catalog XML feed.

Public per-store product feed at ``/api/v1/storefront/meta-feed/{subdomain}.xml``.
Meta's catalog crawler (and Facebook Marketing API "Data Feed Source")
fetches this URL on a schedule and ingests every product as a catalog
entry that dynamic ads can target.

Format: Google Merchant Center RSS XML (which Meta accepts verbatim).
One ``<item>`` per active, non-archived product. ``g:id`` is the
product's stable UUID; ``g:item_group_id`` is set to the same UUID for
v1 (per-variant entries land in v1.1 when NUMU formalizes the Variant
entity). ``content_ids`` on Pixel/CAPI events must match ``g:id`` so
dynamic ads can match the conversion to a catalog row.

Plan: ``Plans/meta-pixels&CAPI/Meta-pixels&CAPI.md`` Phase 16.

**v1 scope:**
  * On-demand generation per request (no Celery beat cache yet — easy
    to add as a follow-up when traffic warrants).
  * Excludes drafts, archived, and out-of-stock items unless inventory
    is untracked.
  * Returns 404 for unknown subdomains.
  * Currency from store config (EGP default).
  * No auth — Meta's crawler hits the URL anonymously, which is the
    standard pattern for catalog feeds.

**v1.1 follow-ups:** Marketing API push (requires Phase 17 OAuth);
per-variant entries; sitemap discovery hint; Cloudflare cache.
"""

from __future__ import annotations

from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_store_repository
from src.api.dependencies.database import get_db
from src.core.entities.product import ProductStatus
from src.core.entities.store import StoreStatus
from src.infrastructure.repositories import StoreRepository

router = APIRouter()


def _build_feed_xml(*, store_name: str, store_url: str, items: list[dict]) -> str:
    """Render the RSS 2.0 + g: namespace XML Meta's catalog crawler expects.

    All merchant-supplied strings go through ``html.escape`` so titles
    with ``&``/``<``/``>`` don't break the XML — Meta rejects malformed
    feeds outright with no partial-ingest.
    """
    item_xml = "\n".join(_item_xml(it) for it in items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">\n'
        "  <channel>\n"
        f"    <title>{escape(store_name)}</title>\n"
        f"    <link>{escape(store_url)}</link>\n"
        "    <description>Product catalog</description>\n"
        f"{item_xml}\n"
        "  </channel>\n"
        "</rss>\n"
    )


def _item_xml(item: dict) -> str:
    parts: list[str] = ["    <item>"]
    parts.append(f"      <g:id>{escape(item['id'])}</g:id>")
    parts.append(
        f"      <g:item_group_id>{escape(item['item_group_id'])}</g:item_group_id>"
    )
    parts.append(f"      <g:title>{escape(item['title'])}</g:title>")
    if item.get("description"):
        parts.append(
            f"      <g:description>{escape(item['description'])}</g:description>"
        )
    parts.append(f"      <g:link>{escape(item['link'])}</g:link>")
    if item.get("image_link"):
        parts.append(f"      <g:image_link>{escape(item['image_link'])}</g:image_link>")
    parts.append(
        f"      <g:availability>{escape(item['availability'])}</g:availability>"
    )
    parts.append(
        f"      <g:condition>{escape(item.get('condition', 'new'))}</g:condition>"
    )
    parts.append(f"      <g:price>{escape(item['price'])}</g:price>")
    if item.get("brand"):
        parts.append(f"      <g:brand>{escape(item['brand'])}</g:brand>")
    if item.get("sale_price"):
        parts.append(f"      <g:sale_price>{escape(item['sale_price'])}</g:sale_price>")
    if item.get("product_type"):
        parts.append(
            f"      <g:product_type>{escape(item['product_type'])}</g:product_type>"
        )
    if item.get("google_product_category"):
        parts.append(
            "      <g:google_product_category>"
            f"{escape(item['google_product_category'])}"
            "</g:google_product_category>"
        )
    if item.get("gtin"):
        parts.append(f"      <g:gtin>{escape(item['gtin'])}</g:gtin>")
    # `mpn` is the MANUFACTURER's part number. A merchant SKU is our identifier,
    # not the manufacturer's, so emitting it here asserted something false about
    # every product; g:id already carries the merchant identifier.
    if item.get("mpn"):
        parts.append(f"      <g:mpn>{escape(item['mpn'])}</g:mpn>")
    for url in item.get("additional_image_links") or []:
        parts.append(
            f"      <g:additional_image_link>{escape(url)}</g:additional_image_link>"
        )
    if item.get("shipping"):
        ship = item["shipping"]
        parts.append("      <g:shipping>")
        parts.append(f"        <g:country>{escape(ship['country'])}</g:country>")
        parts.append(f"        <g:price>{escape(ship['price'])}</g:price>")
        parts.append("      </g:shipping>")
    parts.append("    </item>")
    return "\n".join(parts)


def _store_shipping(store: object, currency: str) -> dict | None:
    """One representative shipping rate for the feed.

    Google lists shipping settings as a REQUIREMENT in 30+ countries, with the
    ``shipping`` attribute as the alternative — so its absence can block free
    listings rather than merely degrade them. We publish the store's cheapest
    configured zone rate as the baseline; anything more precise needs per-region
    rows Google would rather get from account-level settings anyway.

    Returns None when the store has no usable rate, which is honest: a wrong
    shipping price is worse than none.
    """
    settings = getattr(store, "settings", None)
    if not isinstance(settings, dict):
        return None
    shipping = settings.get("shipping")
    if not isinstance(shipping, dict):
        return None
    zones = shipping.get("zones")
    if not isinstance(zones, list):
        return None
    rates: list[float] = []
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        try:
            rates.append(float(zone.get("rate")))
        except (TypeError, ValueError):
            continue
    if not rates:
        return None
    country = (getattr(store, "country", None) or "EG").upper()
    return {"country": country, "price": f"{min(rates):.2f} {currency.upper()}"}


def _product_to_feed_item(
    product: dict, *, store_url: str, currency: str, shipping: dict | None = None
) -> dict | None:
    """Map one row from ``products`` table → feed entry. Returns None
    when the product should be excluded (out of stock + tracked, etc.).
    """
    # In stock when inventory is untracked OR quantity > 0.
    quantity = int(product.get("quantity") or 0)
    track_inventory = bool(product.get("track_inventory", True))
    availability = (
        "in stock" if (not track_inventory or quantity > 0) else "out of stock"
    )

    # Skip drafts entirely — they're not visible on the storefront, so
    # Meta indexing them would surface stale candidates in dynamic ads.
    if product.get("status") and str(product["status"]).lower() == "draft":
        return None

    price_cents = int(product.get("price_amount") or 0)
    price_decimal = f"{price_cents / 100:.2f}"

    # compare_at_price is the "was" price, so when it is higher the CURRENT
    # price is the sale. Meta/Google want g:price = original, g:sale_price =
    # what the shopper pays; without this a discounted product advertised its
    # full price and the strike-through never rendered.
    compare_cents = int(product.get("compare_at_price") or 0)
    if compare_cents > price_cents:
        list_price = f"{compare_cents / 100:.2f} {currency.upper()}"
        sale_price: str | None = f"{price_decimal} {currency.upper()}"
    else:
        list_price = f"{price_decimal} {currency.upper()}"
        sale_price = None

    attrs = (
        product.get("attributes") if isinstance(product.get("attributes"), dict) else {}
    )
    attrs = attrs or {}

    images = product.get("images") or []
    image_link = images[0] if images else None

    # Canonical PDP URL on the store's real public host (custom domain or
    # `<subdomain>.numueg.app`). Meta rejects feeds whose g:link doesn't
    # resolve, so this must match where the storefront actually lives.
    # Slug-first to match the PDP's canonical `/products/<slug>` URL; the
    # backend PDP endpoint accepts a UUID too, so the id fallback still works.
    product_url = f"{store_url}/products/{product.get('slug') or product['id']}"

    # Use meta_catalog_id when the merchant has set one (Phase 8); else
    # fall back to the internal UUID. Either way, the Pixel/CAPI events
    # already use the same value via the storefront's `meta_catalog_id`
    # || product.id fallback, so dedup is consistent.
    feed_id = product.get("meta_catalog_id") or str(product["id"])

    return {
        "id": feed_id,
        # For v1 (no variants), item_group_id = id so dynamic ads can
        # still group by parent. When variants formalize, this becomes
        # the parent product UUID.
        "item_group_id": str(product["id"]),
        "title": str(product.get("name") or "")[:150],
        "description": str(
            product.get("description") or product.get("short_description") or ""
        )[:5000],
        "link": product_url,
        "image_link": image_link,
        "availability": availability,
        "condition": "new",
        "price": list_price,
        "sale_price": sale_price,
        # Real column first; `attributes.brand` stays as the legacy fallback so
        # merchants who set it through the API before the column existed don't
        # lose it.
        "brand": product.get("brand") or attrs.get("brand"),
        "product_type": attrs.get("product_type"),
        # Google's taxonomy — a required-ish quality signal for free listings.
        "google_product_category": attrs.get("google_product_category"),
        # Real barcodes only. A GTIN is issued by GS1; a merchant SKU is not
        # one, and submitting a fabricated value gets items disapproved.
        "gtin": attrs.get("gtin"),
        "mpn": attrs.get("mpn"),
        "additional_image_links": [str(u) for u in images[1:11] if u],
        "shipping": shipping,
    }


@router.get(
    "/meta-feed/{subdomain}.xml",
    summary="Public Meta Commerce Catalog XML feed",
    operation_id="meta_feed_xml",
    responses={
        200: {
            "content": {"application/xml": {}},
            "description": "RSS 2.0 + g: namespace feed Meta's catalog crawler ingests",
        },
        404: {"description": "Store not found or not published"},
    },
)
async def meta_catalog_feed(
    subdomain: Annotated[str, Path(description="Store subdomain")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Serve the product catalog as a Meta-compatible RSS XML feed.

    Uses raw SQL via the existing connection pool to avoid loading the
    full SQLAlchemy ORM mapper graph at startup — keeps the endpoint
    fast and decoupled from the Product entity's evolution.

    Cached at the HTTP layer (Cache-Control: max-age=3600) — Meta's
    crawler respects standard cache headers, so this prevents thundering
    herds on the small minority of merchants whose feed is large.
    """
    normalized = subdomain.lower()
    store = await store_repo.get_by_subdomain(normalized)
    if not store or store.status == StoreStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store not found",
        )

    # Query products with raw SQL — we could use the ProductRepository but it
    # returns entity objects and we just need the field bag.
    #
    # The session is injected rather than reached for. This used to read
    # `store_repo._session`, which does not exist: StoreRepository assigns
    # `self.session`, and no class in its MRO defines the underscored name — so
    # the line raised AttributeError before a single query ran and BOTH feeds
    # returned HTTP 500 for every store, in production, since they shipped.
    # `get_db` is what `get_store_repository` consumes, so this is the same
    # request-scoped session, just obtained through the public contract.
    from sqlalchemy import text

    await session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(store.tenant_id)},
    )
    rows = await session.execute(
        text(
            """
            SELECT id::text AS id, slug, name, description, short_description, sku,
                   price_amount, compare_at_price, brand,
                   status::text AS status, quantity,
                   images, attributes, meta_catalog_id,
                   COALESCE((attributes->>'track_inventory')::boolean, true) AS track_inventory
            FROM public.products
            WHERE store_id = :sid
              AND status = :active_status
            ORDER BY created_at DESC
            LIMIT 5000
            """
        ),
        # The productstatus PG enum stores member NAMES ("ACTIVE"), not
        # values ("active") — no values_callable on the column. Binding
        # .value here made every feed request 500 with
        # `invalid input value for enum productstatus: "active"`.
        {"sid": str(store.id), "active_status": ProductStatus.ACTIVE.name},
    )
    products_raw = [dict(r._mapping) for r in rows.fetchall()]

    currency = (getattr(store, "default_currency", None) or "EGP").upper()
    store_url = store.store_url
    shipping = _store_shipping(store, currency)
    items: list[dict] = []
    for p in products_raw:
        entry = _product_to_feed_item(
            p, store_url=store_url, currency=currency, shipping=shipping
        )
        if entry is not None:
            items.append(entry)

    xml = _build_feed_xml(
        store_name=store.name,
        store_url=store_url,
        items=items,
    )
    return Response(
        content=xml,
        media_type="application/xml",
        headers={
            # 1-hour browser/CDN cache — matches the planned Celery beat
            # refresh cadence so Meta's crawler doesn't out-pace
            # availability/price updates on a freshly-edited product.
            "Cache-Control": "public, max-age=3600",
        },
    )
