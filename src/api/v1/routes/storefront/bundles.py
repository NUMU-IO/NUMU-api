"""Storefront bundle routes (public, customer-facing).

URL: /storefront/store/{store_id}/products/{product_id}/bundles

Returns the "Frequently Bought Together" widget data for a product page.
No authentication required — this is a public endpoint.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel

from src.api.dependencies.repositories import (
    get_product_bundle_repository,
    get_product_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories import ProductRepository, StoreRepository
from src.infrastructure.repositories.product_bundle_repository import (
    ProductBundleRepository,
)

router = APIRouter()


# ── Response Schemas ──────────────────────────────────────────────────────


class BundledProductResponse(BaseModel):
    """A single bundled product in the widget."""

    id: str
    name: str
    slug: str
    price: int  # cents
    price_currency: str
    compare_at_price: int | None
    image: str | None
    is_in_stock: bool
    quantity: int


class BundleWidgetItem(BaseModel):
    """A single item in the FBT widget."""

    bundle_id: str
    product: BundledProductResponse
    discount_type: str  # "none" | "percentage" | "fixed"
    discount_value: int
    discounted_price: int  # pre-calculated for the frontend
    selected: bool  # default checked state


class BundleWidgetResponse(BaseModel):
    """Complete FBT widget data for a product page."""

    section_title_en: str
    section_title_ar: str
    primary_product: BundledProductResponse
    bundles: list[BundleWidgetItem]
    total_original: int  # sum of all prices (primary + all bundles)
    total_discounted: int  # sum after applying bundle discounts


# ── Helpers ───────────────────────────────────────────────────────────────


def _calculate_discounted_price(
    original_price: int, discount_type: str, discount_value: int
) -> int:
    """Calculate the discounted price for a bundled product."""
    if discount_type == "percentage" and discount_value > 0:
        discount_amount = (original_price * discount_value) // 100
        return max(0, original_price - discount_amount)
    elif discount_type == "fixed" and discount_value > 0:
        return max(0, original_price - discount_value)
    return original_price


def _product_response(product) -> BundledProductResponse:
    """Convert a product to a storefront-safe response.

    Accepts either a ``ProductModel`` (columns ``price_amount``,
    ``price_currency``, ``compare_at_price: int | None``) or a domain
    ``Product`` entity (``price: Money``, ``compare_at_price: Money | None``).
    We dispatch on attribute presence so the caller can pass whichever
    shape is cheaper to get. Mixing them silently was the root cause of
    the AttributeError on this endpoint.
    """
    # Domain entity path — Money-based
    if hasattr(product, "price_amount"):
        price_cents = product.price_amount
        price_currency = product.price_currency
        compare_at = product.compare_at_price
    else:
        price_cents = product.price.cents
        price_currency = product.price.currency.value
        compare_at = (
            product.compare_at_price.cents if product.compare_at_price else None
        )

    return BundledProductResponse(
        id=str(product.id),
        name=product.name,
        slug=product.slug,
        price=price_cents,
        price_currency=price_currency,
        compare_at_price=compare_at,
        image=product.images[0] if product.images else None,
        is_in_stock=product.quantity > 0,
        quantity=product.quantity,
    )


# ── Route ─────────────────────────────────────────────────────────────────


@router.get(
    "/products/{product_id}/bundles",
    response_model=SuccessResponse[BundleWidgetResponse],
    summary="Get FBT bundles for a product",
    operation_id="get_storefront_product_bundles",
)
async def get_product_bundles(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_id: Annotated[UUID, Path(description="Product ID")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    bundle_repo: Annotated[
        ProductBundleRepository, Depends(get_product_bundle_repository)
    ],
):
    """Return the Frequently Bought Together widget data for a product page.

    Only returns active bundles where the bundled product is in stock
    and has status = active. Prices are pre-calculated for the frontend.
    """
    # Verify store exists
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    # Verify primary product exists
    product = await product_repo.get_by_id(product_id)
    if not product or product.store_id != store_id:
        raise EntityNotFoundError("Product", str(product_id))

    # Fetch active bundles
    bundles = await bundle_repo.list_by_primary_product(
        store_id=store_id,
        primary_product_id=product_id,
        active_only=True,
    )

    # Build widget items — filter out out-of-stock or inactive products
    widget_items: list[BundleWidgetItem] = []
    section_title_en = "Frequently Bought Together"
    section_title_ar = "كثيرًا ما يُشترى معًا"

    for bundle in bundles:
        bp = bundle.bundled_product
        if not bp or bp.quantity <= 0:
            continue  # Skip out-of-stock bundled products

        # Use custom section title from the first bundle that has one
        if bundle.section_title_en and section_title_en == "Frequently Bought Together":
            section_title_en = bundle.section_title_en
        if bundle.section_title_ar and section_title_ar == "كثيرًا ما يُشترى معًا":
            section_title_ar = bundle.section_title_ar

        discounted_price = _calculate_discounted_price(
            bp.price_amount, bundle.discount_type, bundle.discount_value
        )

        widget_items.append(
            BundleWidgetItem(
                bundle_id=str(bundle.id),
                product=_product_response(bp),
                discount_type=bundle.discount_type,
                discount_value=bundle.discount_value,
                discounted_price=discounted_price,
                selected=True,  # default: all checked
            )
        )

    # Calculate totals. `product` here is the domain entity from
    # product_repo.get_by_id() — it exposes price as a Money value object
    # (`price.cents`), not the DB column name `price_amount` that the
    # SQLAlchemy ProductModel has. Using the wrong one raised
    # AttributeError: 'Product' object has no attribute 'price_amount'.
    primary_price = product.price.cents
    total_original = primary_price + sum(item.product.price for item in widget_items)
    total_discounted = primary_price + sum(
        item.discounted_price for item in widget_items
    )

    return SuccessResponse(
        data=BundleWidgetResponse(
            section_title_en=section_title_en,
            section_title_ar=section_title_ar,
            primary_product=_product_response(product),
            bundles=widget_items,
            total_original=total_original,
            total_discounted=total_discounted,
        ),
        message="Bundles retrieved successfully",
    )
